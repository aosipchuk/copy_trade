from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import get_logger
from app.models.new_wallet import (
    NewWalletCandidate,
    UserNewWalletItem,
    UserNewWalletSubscription,
)
from app.models.subscription import Subscription
from app.models.user import User, UserAgent
from app.schemas.new_wallet import NewWalletSubscriptionCreate
from app.schemas.subscription import SubscriptionCreate
from app.services.hyperliquid.info_client import HyperliquidInfoClient
from app.services.subscription_service import create_subscription

logger = get_logger(__name__)


def utcnow() -> datetime:
    return datetime.now(tz=UTC).replace(tzinfo=None)


async def create_or_reactivate_new_wallet_subscription(
    db: AsyncSession,
    *,
    user: User,
    data: NewWalletSubscriptionCreate,
) -> UserNewWalletSubscription:
    if data.max_active_wallets > settings.new_wallet_max_active_per_user:
        raise ValueError(
            "max_active_wallets cannot exceed "
            f"{settings.new_wallet_max_active_per_user}"
        )
    if not data.is_demo:
        if not data.risk_disclosure_accepted:
            raise ValueError("Risk disclosure must be accepted for live activation")
        await _require_live_wallet_and_agent(db, user)

    parent_result = await db.execute(
        select(UserNewWalletSubscription).where(
            UserNewWalletSubscription.user_id == user.id,
            UserNewWalletSubscription.is_demo.is_(data.is_demo),
            UserNewWalletSubscription.status != "canceled",
        )
    )
    parent = parent_result.scalar_one_or_none()
    if parent is None:
        parent = UserNewWalletSubscription(
            user_id=user.id,
            status="active",
            is_demo=data.is_demo,
            total_allocation_usd=data.total_allocation_usd,
            max_active_wallets=data.max_active_wallets,
            max_per_wallet_usd=data.max_per_wallet_usd,
            copy_ratio_pct=data.copy_ratio_pct,
            stop_loss_pct=data.stop_loss_pct,
            max_leverage=data.max_leverage,
            sizing_mode=data.sizing_mode,
            allowed_coins=data.allowed_coins,
            close_positions_on_expire=True,
        )
        db.add(parent)
    else:
        parent.status = "active"
        parent.total_allocation_usd = data.total_allocation_usd
        parent.max_active_wallets = data.max_active_wallets
        parent.max_per_wallet_usd = data.max_per_wallet_usd
        parent.copy_ratio_pct = data.copy_ratio_pct
        parent.stop_loss_pct = data.stop_loss_pct
        parent.max_leverage = data.max_leverage
        parent.sizing_mode = data.sizing_mode
        parent.allowed_coins = data.allowed_coins
        parent.close_positions_on_expire = True
        parent.canceled_at = None

    await db.flush()
    if (
        settings.new_wallet_discovery_enabled
        and settings.new_wallet_auto_attach_enabled
    ):
        await attach_qualified_new_wallets_for_parent(db, parent, user=user)
    return parent


async def attach_qualified_new_wallets_for_parent(
    db: AsyncSession,
    parent: UserNewWalletSubscription,
    *,
    user: User | None = None,
) -> int:
    if parent.status != "active":
        return 0

    user_obj = user
    if user_obj is None:
        user_result = await db.execute(select(User).where(User.id == parent.user_id))
        user_obj = user_result.scalar_one_or_none()
    if user_obj is None:
        return 0

    active_count = await _active_item_count(db, parent.id)
    available_slots = max(0, int(parent.max_active_wallets) - active_count)
    if available_slots <= 0:
        return 0

    if not parent.is_demo:
        await _require_live_wallet_and_agent(db, user_obj)
        margin_summary = await HyperliquidInfoClient().get_account_summary(
            user_obj.hl_address or ""
        )
    else:
        margin_summary = None

    attached = 0
    candidates = await _eligible_candidates(db, parent.id, limit=available_slots)
    for candidate in candidates:
        if candidate.trader_id is None:
            continue
        target_allocation = _target_allocation(parent)
        expires_at = utcnow() + timedelta(
            days=settings.new_wallet_subscription_ttl_days
        )
        try:
            child = await create_subscription(
                db,
                parent.user_id,
                SubscriptionCreate(
                    trader_id=candidate.trader_id,
                    max_allocation_usd=float(target_allocation),
                    copy_ratio_pct=float(parent.copy_ratio_pct),
                    stop_loss_pct=float(parent.stop_loss_pct),
                    max_leverage=float(parent.max_leverage),
                    sizing_mode=parent.sizing_mode,  # type: ignore[arg-type]
                    allowed_coins=parent.allowed_coins,
                    is_demo=parent.is_demo,
                ),
                user_obj.hl_address,
                source_type="new_wallet",
                source_id=parent.id,
                margin_summary=margin_summary,
                expires_at=expires_at,
            )
            db.add(
                UserNewWalletItem(
                    user_new_wallet_subscription_id=parent.id,
                    candidate_id=candidate.id,
                    subscription_id=child.id,
                    trader_id=candidate.trader_id,
                    target_allocation_usd=float(target_allocation),
                    status="active",
                    expires_at=expires_at,
                )
            )
            candidate.status = "subscribed"
            attached += 1
            logger.info(
                "new_wallet_user_attached",
                parent_id=parent.id,
                candidate_id=candidate.id,
                child_subscription_id=child.id,
                user_id=parent.user_id,
            )
        except Exception as exc:
            logger.warning(
                "new_wallet_attach_failed",
                parent_id=parent.id,
                candidate_id=candidate.id,
                error=str(exc),
            )
    await db.flush()
    return attached


async def attach_qualified_new_wallets(db: AsyncSession) -> int:
    if (
        not settings.new_wallet_discovery_enabled
        or not settings.new_wallet_auto_attach_enabled
    ):
        return 0
    parent_result = await db.execute(
        select(UserNewWalletSubscription)
        .where(UserNewWalletSubscription.status == "active")
        .order_by(UserNewWalletSubscription.created_at.asc())
        .limit(settings.new_wallet_max_attach_per_run)
    )
    total = 0
    for parent in parent_result.scalars().all():
        total += await attach_qualified_new_wallets_for_parent(db, parent)
    return total


async def list_user_new_wallet_subscriptions(
    db: AsyncSession,
    *,
    user_id: int,
) -> list[UserNewWalletSubscription]:
    result = await db.execute(
        select(UserNewWalletSubscription)
        .where(UserNewWalletSubscription.user_id == user_id)
        .order_by(UserNewWalletSubscription.created_at.desc())
    )
    return list(result.scalars().all())


async def get_user_new_wallet_subscription(
    db: AsyncSession,
    *,
    user_id: int,
    subscription_id: int,
) -> UserNewWalletSubscription:
    result = await db.execute(
        select(UserNewWalletSubscription).where(
            UserNewWalletSubscription.id == subscription_id,
            UserNewWalletSubscription.user_id == user_id,
        )
    )
    parent = result.scalar_one_or_none()
    if parent is None:
        raise LookupError("New wallet subscription not found")
    return parent


async def cancel_user_new_wallet_subscription(
    db: AsyncSession,
    *,
    user_id: int,
    subscription_id: int,
    close_positions: bool = True,
) -> UserNewWalletSubscription:
    parent = await get_user_new_wallet_subscription(
        db,
        user_id=user_id,
        subscription_id=subscription_id,
    )
    parent.status = "canceled"
    parent.canceled_at = utcnow()

    items_result = await db.execute(
        select(UserNewWalletItem, Subscription)
        .join(Subscription, Subscription.id == UserNewWalletItem.subscription_id)
        .where(
            UserNewWalletItem.user_new_wallet_subscription_id == parent.id,
            UserNewWalletItem.status == "active",
        )
    )
    live_to_close: list[int] = []
    for item, child in items_result.all():
        item.status = "removed"
        item.ended_at = utcnow()
        child.is_active = False
        child.ended_reason = "new_wallet_parent_canceled"
        if close_positions:
            if child.is_demo:
                from app.services.demo_service import close_demo_subscription_positions

                await close_demo_subscription_positions(db, child)
            else:
                live_to_close.append(child.id)
    await db.flush()

    if close_positions and live_to_close:
        from app.tasks.execution_tasks import close_subscription_positions_async

        for child_id in live_to_close:
            asyncio.create_task(close_subscription_positions_async(user_id, child_id))

    return parent


async def _require_live_wallet_and_agent(db: AsyncSession, user: User) -> None:
    if not user.hl_address:
        raise ValueError("HL wallet address required for live new-wallet strategy")
    agent_result = await db.execute(
        select(UserAgent).where(
            UserAgent.user_id == user.id,
            UserAgent.is_active.is_(True),
            UserAgent.approved_at.is_not(None),
        )
    )
    if agent_result.scalar_one_or_none() is None:
        raise ValueError("Approved HL agent required for live new-wallet strategy")


async def _active_item_count(db: AsyncSession, parent_id: int) -> int:
    result = await db.execute(
        select(func.count(UserNewWalletItem.id)).where(
            UserNewWalletItem.user_new_wallet_subscription_id == parent_id,
            UserNewWalletItem.status == "active",
        )
    )
    return int(result.scalar_one())


async def _eligible_candidates(
    db: AsyncSession,
    parent_id: int,
    *,
    limit: int,
) -> list[NewWalletCandidate]:
    already_attached = select(UserNewWalletItem.candidate_id).where(
        UserNewWalletItem.user_new_wallet_subscription_id == parent_id,
        UserNewWalletItem.status.in_(("active", "expired", "removed")),
    )
    result = await db.execute(
        select(NewWalletCandidate)
        .where(
            NewWalletCandidate.status.in_(("qualified", "subscribed")),
            NewWalletCandidate.trader_id.is_not(None),
            NewWalletCandidate.id.not_in(already_attached),
        )
        .order_by(NewWalletCandidate.qualified_at.asc().nulls_last())
        .limit(limit)
    )
    return list(result.scalars().all())


def _target_allocation(parent: UserNewWalletSubscription) -> Decimal:
    per_slot = Decimal(str(parent.total_allocation_usd)) / Decimal(
        str(parent.max_active_wallets)
    )
    return min(Decimal(str(parent.max_per_wallet_usd)), per_slot)
