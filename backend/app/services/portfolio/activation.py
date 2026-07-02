from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.logging import get_logger
from app.models.portfolio import (
    ModelPortfolio,
    ModelPortfolioAllocation,
    ModelPortfolioVersion,
    UserPortfolioItem,
    UserPortfolioSubscription,
)
from app.models.subscription import Subscription
from app.models.trader import Trader
from app.models.user import User, UserAgent
from app.schemas.portfolio import (
    PortfolioActivationConflict,
    UserPortfolioActivationResponse,
    UserPortfolioItemDetailResponse,
    UserPortfolioItemResponse,
    UserPortfolioSubscriptionCreate,
    UserPortfolioSubscriptionDetailResponse,
    UserPortfolioSubscriptionResponse,
)
from app.schemas.subscription import SubscriptionCreate
from app.services.hyperliquid.info_client import HyperliquidInfoClient
from app.services.hyperliquid.models import MarginSummary
from app.services.portfolio.access import user_can_view_portfolio_trader_identities
from app.services.portfolio.billing import (
    require_live_portfolio_billing,
    user_has_beta_override,
)
from app.services.portfolio.subscription_lifecycle import (
    deactivate_portfolio_owned_subscriptions,
    lock_user_portfolio_subscription_slot,
)
from app.services.subscription_service import (
    _to_response as subscription_to_response,
)
from app.services.subscription_service import (
    create_subscription,
)

logger = get_logger(__name__)

ACTIVE_USER_PORTFOLIO_STATUSES = ("trialing", "active", "past_due", "paused")


@dataclass(frozen=True)
class PortfolioActivationResult:
    detail: UserPortfolioSubscriptionDetailResponse
    created: bool
    conflicts: list[PortfolioActivationConflict]

    def to_response(self) -> UserPortfolioActivationResponse:
        return UserPortfolioActivationResponse(
            **self.detail.model_dump(),
            created=self.created,
            conflicts=self.conflicts,
        )


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _decimal(value: object) -> Decimal:
    return Decimal(str(value))


def _total_allocation(value: float) -> Decimal:
    return _decimal(value).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _sorted_allocations(
    allocations: Iterable[ModelPortfolioAllocation],
) -> list[ModelPortfolioAllocation]:
    return sorted(
        allocations,
        key=lambda item: (_decimal(item.target_weight_pct), item.id),
        reverse=True,
    )


def _validate_allocations(allocations: list[ModelPortfolioAllocation]) -> None:
    if not allocations:
        raise ValueError("Published portfolio version has no allocations.")

    total_weight = sum(
        (_decimal(allocation.target_weight_pct) for allocation in allocations),
        Decimal("0"),
    )
    if abs(total_weight - Decimal("100.000")) > Decimal("0.001"):
        raise ValueError(
            "Published portfolio version allocations must sum to 100% before "
            "activation."
        )

    inactive_traders = [
        allocation.trader_id
        for allocation in allocations
        if allocation.trader is None or not allocation.trader.is_active
    ]
    if inactive_traders:
        raise ValueError(
            "Published portfolio version contains inactive traders: "
            + ", ".join(str(trader_id) for trader_id in inactive_traders)
        )


def _target_allocations(
    total_allocation_usd: float,
    allocations: list[ModelPortfolioAllocation],
) -> dict[int, Decimal]:
    total = _total_allocation(total_allocation_usd)
    targets: dict[int, Decimal] = {}
    assigned = Decimal("0.00")

    for allocation in allocations[:-1]:
        target = (
            total * _decimal(allocation.target_weight_pct) / Decimal("100")
        ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        targets[allocation.id] = target
        assigned += target

    last = allocations[-1]
    targets[last.id] = (total - assigned).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )
    if targets[last.id] <= Decimal("0"):
        raise ValueError("Portfolio allocation is too small for the published weights.")

    return targets


async def _load_published_version(
    db: AsyncSession,
    portfolio_id: int,
    active_version_id: int,
) -> tuple[ModelPortfolio, ModelPortfolioVersion]:
    result = await db.execute(
        select(ModelPortfolio, ModelPortfolioVersion)
        .join(
            ModelPortfolioVersion,
            ModelPortfolioVersion.portfolio_id == ModelPortfolio.id,
        )
        .options(
            selectinload(ModelPortfolioVersion.allocations).selectinload(
                ModelPortfolioAllocation.trader
            )
        )
        .where(
            ModelPortfolio.id == portfolio_id,
            ModelPortfolio.status == "active",
            ModelPortfolioVersion.id == active_version_id,
            ModelPortfolioVersion.status == "published",
            ModelPortfolioVersion.valid_to.is_(None),
        )
    )
    row = result.one_or_none()
    if row is None:
        raise LookupError("Published model portfolio version not found.")
    portfolio, version = row
    return portfolio, version


async def _find_existing_active_demo(
    db: AsyncSession,
    user_id: int,
    portfolio_id: int,
    active_version_id: int,
) -> UserPortfolioSubscription | None:
    result = await db.execute(
        select(UserPortfolioSubscription)
        .where(
            UserPortfolioSubscription.user_id == user_id,
            UserPortfolioSubscription.portfolio_id == portfolio_id,
            UserPortfolioSubscription.active_version_id == active_version_id,
            UserPortfolioSubscription.is_demo.is_(True),
            UserPortfolioSubscription.status.in_(ACTIVE_USER_PORTFOLIO_STATUSES),
        )
        .order_by(UserPortfolioSubscription.id.asc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def _find_existing_live(
    db: AsyncSession,
    user_id: int,
    portfolio_id: int,
    active_version_id: int,
) -> UserPortfolioSubscription | None:
    result = await db.execute(
        select(UserPortfolioSubscription)
        .where(
            UserPortfolioSubscription.user_id == user_id,
            UserPortfolioSubscription.portfolio_id == portfolio_id,
            UserPortfolioSubscription.active_version_id == active_version_id,
            UserPortfolioSubscription.is_demo.is_(False),
            UserPortfolioSubscription.status != "canceled",
        )
        .order_by(
            UserPortfolioSubscription.created_at.desc(),
            UserPortfolioSubscription.id.desc(),
        )
        .limit(1)
    )
    return result.scalar_one_or_none()


async def _active_item_count(
    db: AsyncSession,
    user_portfolio_subscription_id: int,
) -> int:
    result = await db.execute(
        select(func.count(UserPortfolioItem.id)).where(
            UserPortfolioItem.user_portfolio_subscription_id
            == user_portfolio_subscription_id,
            UserPortfolioItem.status == "active",
        )
    )
    return int(result.scalar_one())


async def detect_manual_live_conflicts(
    db: AsyncSession,
    user_id: int,
    trader_ids: Iterable[int],
) -> list[PortfolioActivationConflict]:
    trader_id_list = list(trader_ids)
    if not trader_id_list:
        return []

    result = await db.execute(
        select(Subscription, Trader)
        .join(Trader, Trader.id == Subscription.trader_id)
        .where(
            Subscription.user_id == user_id,
            Subscription.trader_id.in_(trader_id_list),
            Subscription.is_active.is_(True),
            Subscription.is_demo.is_(False),
            Subscription.source_type == "manual",
            Subscription.managed_by_portfolio.is_(False),
        )
        .order_by(Trader.display_name.asc().nulls_last(), Trader.hl_address.asc())
    )
    return [
        PortfolioActivationConflict(
            trader_id=subscription.trader_id,
            trader_address=trader.hl_address,
            trader_display_name=trader.display_name,
            subscription_id=subscription.id,
            is_demo=subscription.is_demo,
        )
        for subscription, trader in result.all()
    ]


async def _load_detail(
    db: AsyncSession,
    user_id: int,
    user_portfolio_subscription_id: int,
) -> UserPortfolioSubscriptionDetailResponse:
    result = await db.execute(
        select(UserPortfolioSubscription, ModelPortfolio, ModelPortfolioVersion)
        .join(
            ModelPortfolio,
            ModelPortfolio.id == UserPortfolioSubscription.portfolio_id,
        )
        .join(
            ModelPortfolioVersion,
            ModelPortfolioVersion.id == UserPortfolioSubscription.active_version_id,
        )
        .where(
            UserPortfolioSubscription.id == user_portfolio_subscription_id,
            UserPortfolioSubscription.user_id == user_id,
        )
    )
    row = result.one_or_none()
    if row is None:
        raise LookupError("Portfolio subscription not found.")

    portfolio_subscription, portfolio, version = row
    include_trader_identity = await user_can_view_portfolio_trader_identities(
        db,
        user_id,
        portfolio.id,
        version.id,
    )
    item_result = await db.execute(
        select(UserPortfolioItem, Subscription, Trader)
        .join(Subscription, Subscription.id == UserPortfolioItem.subscription_id)
        .join(Trader, Trader.id == UserPortfolioItem.trader_id)
        .where(
            UserPortfolioItem.user_portfolio_subscription_id
            == portfolio_subscription.id
        )
        .order_by(UserPortfolioItem.id.asc())
    )

    items: list[UserPortfolioItemDetailResponse] = []
    for item, subscription, trader in item_result.all():
        item_payload = UserPortfolioItemResponse.model_validate(item).model_dump()
        item_payload["trader_id"] = item.trader_id if include_trader_identity else None
        items.append(
            UserPortfolioItemDetailResponse(
                **item_payload,
                subscription=await subscription_to_response(
                    db,
                    subscription,
                    include_trader_identity=include_trader_identity,
                ),
                trader_address=trader.hl_address if include_trader_identity else None,
                trader_display_name=(
                    trader.display_name if include_trader_identity else None
                ),
            )
        )

    return UserPortfolioSubscriptionDetailResponse(
        **UserPortfolioSubscriptionResponse.model_validate(
            portfolio_subscription
        ).model_dump(),
        portfolio_slug=portfolio.slug,
        portfolio_name=portfolio.name,
        active_version_no=version.version_no,
        trader_details_visible=include_trader_identity,
        items=items,
    )


async def list_user_portfolio_subscriptions(
    db: AsyncSession,
    user_id: int,
    *,
    is_demo: bool | None = None,
    portfolio_id: int | None = None,
    active_only: bool = True,
) -> list[UserPortfolioSubscriptionDetailResponse]:
    statement = select(UserPortfolioSubscription.id).where(
        UserPortfolioSubscription.user_id == user_id
    )
    if is_demo is not None:
        statement = statement.where(UserPortfolioSubscription.is_demo.is_(is_demo))
    if portfolio_id is not None:
        statement = statement.where(
            UserPortfolioSubscription.portfolio_id == portfolio_id
        )
    if active_only:
        statement = statement.where(
            UserPortfolioSubscription.status.in_(ACTIVE_USER_PORTFOLIO_STATUSES)
        )
    statement = statement.order_by(UserPortfolioSubscription.created_at.desc())

    result = await db.execute(statement)
    ids = list(result.scalars().all())
    return [
        await _load_detail(db, user_id, user_portfolio_subscription_id)
        for user_portfolio_subscription_id in ids
    ]


async def get_user_portfolio_subscription(
    db: AsyncSession,
    user_id: int,
    user_portfolio_subscription_id: int,
) -> UserPortfolioSubscriptionDetailResponse:
    return await _load_detail(db, user_id, user_portfolio_subscription_id)


async def _load_user(db: AsyncSession, user_id: int) -> User:
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None:
        raise LookupError("User not found.")
    return user


async def _require_live_wallet_ready(db: AsyncSession, user: User) -> str:
    if not user.hl_address:
        raise ValueError("HL wallet address required to activate live model portfolio.")

    result = await db.execute(
        select(UserAgent.id)
        .where(
            UserAgent.user_id == user.id,
            UserAgent.is_active.is_(True),
            UserAgent.approved_at.is_not(None),
        )
        .limit(1)
    )
    if result.scalar_one_or_none() is None:
        raise ValueError(
            "Active Hyperliquid agent required to activate live model portfolio."
        )

    return user.hl_address


async def _fetch_margin_summary(user_hl_address: str, user_id: int) -> MarginSummary:
    try:
        hl = HyperliquidInfoClient()
        return await hl.get_account_summary(user_hl_address)
    except Exception as exc:
        logger.error(
            "portfolio_activation_equity_fetch_failed",
            user_id=user_id,
            error=str(exc),
        )
        raise ValueError("Failed to fetch HL account data — try again later") from exc


def _subscription_create(
    allocation: ModelPortfolioAllocation,
    target_allocation: Decimal,
    *,
    is_demo: bool,
) -> SubscriptionCreate:
    return SubscriptionCreate(
        trader_id=allocation.trader_id,
        max_allocation_usd=float(target_allocation),
        copy_ratio_pct=float(allocation.copy_ratio_pct),
        stop_loss_pct=float(allocation.stop_loss_pct),
        max_leverage=float(allocation.max_leverage),
        sizing_mode=allocation.sizing_mode,
        max_per_coin_usd=(
            float(allocation.max_per_coin_usd)
            if allocation.max_per_coin_usd is not None
            else None
        ),
        allowed_coins=(
            list(allocation.allowed_coins)
            if allocation.allowed_coins is not None
            else None
        ),
        is_demo=is_demo,
    )


async def activate_user_portfolio_subscription(
    db: AsyncSession,
    user_id: int,
    data: UserPortfolioSubscriptionCreate,
) -> PortfolioActivationResult:
    portfolio, version = await _load_published_version(
        db, data.portfolio_id, data.active_version_id
    )
    allocations = _sorted_allocations(version.allocations)
    _validate_allocations(allocations)
    conflicts = await detect_manual_live_conflicts(
        db, user_id, (allocation.trader_id for allocation in allocations)
    )

    if not data.is_demo:
        if conflicts:
            raise ValueError(
                "Live activation has manual subscription conflicts that must be "
                "resolved first."
            )
        await require_live_portfolio_billing(db, user_id, portfolio.id, version.id)
        if not data.risk_disclosure_accepted:
            raise ValueError(
                "Risk disclosure must be accepted before live model portfolio "
                "activation."
            )

        await lock_user_portfolio_subscription_slot(
            db,
            user_id=user_id,
            portfolio_id=portfolio.id,
            active_version_id=version.id,
            is_demo=False,
        )
        existing_live = await _find_existing_live(db, user_id, portfolio.id, version.id)
        if existing_live is not None and await _active_item_count(db, existing_live.id):
            return PortfolioActivationResult(
                detail=await _load_detail(db, user_id, existing_live.id),
                created=False,
                conflicts=[],
            )

        user = await _load_user(db, user_id)
        user_hl_address = await _require_live_wallet_ready(db, user)
        margin_summary = await _fetch_margin_summary(user_hl_address, user_id)

        logger.info(
            "portfolio_activation_started",
            user_id=user_id,
            portfolio_id=portfolio.id,
            version_id=version.id,
            is_demo=False,
        )

        portfolio_subscription = existing_live
        if portfolio_subscription is None:
            portfolio_subscription = UserPortfolioSubscription(
                user_id=user_id,
                portfolio_id=portfolio.id,
                active_version_id=version.id,
                status="active",
                is_demo=False,
                auto_rebalance=data.auto_rebalance,
                total_allocation_usd=data.total_allocation_usd,
                close_removed_positions=data.close_removed_positions,
                billing_provider=(
                    "admin_override" if user_has_beta_override(user) else None
                ),
            )
            db.add(portfolio_subscription)
            await db.flush()
        else:
            portfolio_subscription.status = "active"
            portfolio_subscription.auto_rebalance = data.auto_rebalance
            portfolio_subscription.total_allocation_usd = data.total_allocation_usd
            portfolio_subscription.close_removed_positions = (
                data.close_removed_positions
            )
            if (
                user_has_beta_override(user)
                and portfolio_subscription.billing_provider is None
            ):
                portfolio_subscription.billing_provider = "admin_override"
            await db.flush()

        target_allocations = _target_allocations(data.total_allocation_usd, allocations)
        created_subscription_count = 0
        try:
            for allocation in allocations:
                target_allocation = target_allocations[allocation.id]
                subscription_response = await create_subscription(
                    db,
                    user_id,
                    _subscription_create(allocation, target_allocation, is_demo=False),
                    user_hl_address,
                    source_type="model_portfolio",
                    source_id=portfolio_subscription.id,
                    source_version_id=version.id,
                    managed_by_portfolio=True,
                    margin_summary=margin_summary,
                )
                created_subscription_count += 1

                db.add(
                    UserPortfolioItem(
                        user_portfolio_subscription_id=portfolio_subscription.id,
                        subscription_id=subscription_response.id,
                        portfolio_version_id=version.id,
                        allocation_id=allocation.id,
                        trader_id=allocation.trader_id,
                        target_allocation_usd=float(target_allocation),
                        target_weight_pct=allocation.target_weight_pct,
                        status="active",
                    )
                )
        except ValueError as exc:
            logger.warning(
                "portfolio_activation_failed",
                user_id=user_id,
                portfolio_id=portfolio.id,
                version_id=version.id,
                is_demo=False,
                created_subscription_count=created_subscription_count,
                error=str(exc),
            )
            raise

        await db.flush()
        logger.info(
            "portfolio_activation_completed",
            user_id=user_id,
            portfolio_subscription_id=portfolio_subscription.id,
            generated_subscription_count=len(allocations),
        )
        return PortfolioActivationResult(
            detail=await _load_detail(db, user_id, portfolio_subscription.id),
            created=True,
            conflicts=[],
        )

    await lock_user_portfolio_subscription_slot(
        db,
        user_id=user_id,
        portfolio_id=portfolio.id,
        active_version_id=version.id,
        is_demo=True,
    )
    existing = await _find_existing_active_demo(db, user_id, portfolio.id, version.id)
    if existing is not None:
        return PortfolioActivationResult(
            detail=await _load_detail(db, user_id, existing.id),
            created=False,
            conflicts=conflicts,
        )

    logger.info(
        "portfolio_activation_started",
        user_id=user_id,
        portfolio_id=portfolio.id,
        version_id=version.id,
        is_demo=True,
    )
    portfolio_subscription = UserPortfolioSubscription(
        user_id=user_id,
        portfolio_id=portfolio.id,
        active_version_id=version.id,
        status="active",
        is_demo=True,
        auto_rebalance=data.auto_rebalance,
        total_allocation_usd=data.total_allocation_usd,
        close_removed_positions=data.close_removed_positions,
    )
    db.add(portfolio_subscription)
    await db.flush()

    target_allocations = _target_allocations(data.total_allocation_usd, allocations)
    for allocation in allocations:
        target_allocation = target_allocations[allocation.id]
        subscription_response = await create_subscription(
            db,
            user_id,
            _subscription_create(allocation, target_allocation, is_demo=True),
            user_hl_address=None,
            source_type="model_portfolio",
            source_id=portfolio_subscription.id,
            source_version_id=version.id,
            managed_by_portfolio=True,
        )

        db.add(
            UserPortfolioItem(
                user_portfolio_subscription_id=portfolio_subscription.id,
                subscription_id=subscription_response.id,
                portfolio_version_id=version.id,
                allocation_id=allocation.id,
                trader_id=allocation.trader_id,
                target_allocation_usd=float(target_allocation),
                target_weight_pct=allocation.target_weight_pct,
                status="active",
            )
        )

    await db.flush()
    logger.info(
        "portfolio_activation_completed",
        user_id=user_id,
        portfolio_subscription_id=portfolio_subscription.id,
        generated_subscription_count=len(allocations),
    )
    return PortfolioActivationResult(
        detail=await _load_detail(db, user_id, portfolio_subscription.id),
        created=True,
        conflicts=conflicts,
    )


async def cancel_user_portfolio_subscription(
    db: AsyncSession,
    user_id: int,
    user_portfolio_subscription_id: int,
) -> UserPortfolioSubscriptionDetailResponse:
    result = await db.execute(
        select(UserPortfolioSubscription).where(
            UserPortfolioSubscription.id == user_portfolio_subscription_id,
            UserPortfolioSubscription.user_id == user_id,
        )
    )
    portfolio_subscription = result.scalar_one_or_none()
    if portfolio_subscription is None:
        raise LookupError("Portfolio subscription not found.")
    if portfolio_subscription.status == "canceled":
        return await _load_detail(db, user_id, portfolio_subscription.id)

    now = _now()
    portfolio_subscription.status = "canceled"
    portfolio_subscription.canceled_at = now
    disabled_subscription_count = await deactivate_portfolio_owned_subscriptions(
        db,
        portfolio_subscription,
        close_positions=(
            not portfolio_subscription.is_demo
            and portfolio_subscription.close_removed_positions
        ),
    )

    await db.flush()
    logger.info(
        "portfolio_subscription_canceled",
        user_id=user_id,
        portfolio_subscription_id=portfolio_subscription.id,
        disabled_subscription_count=disabled_subscription_count,
    )
    return await _load_detail(db, user_id, portfolio_subscription.id)
