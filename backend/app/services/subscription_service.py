import asyncio
from datetime import datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.subscription import Subscription
from app.models.trade import UserTrade
from app.models.trader import Trader
from app.schemas.subscription import (
    SubscriptionCreate,
    SubscriptionResponse,
    SubscriptionUpdate,
)
from app.services.hyperliquid.info_client import HyperliquidInfoClient
from app.services.hyperliquid.models import MarginSummary
from app.services.risk_manager import check_portfolio_risk

logger = get_logger(__name__)


async def _compute_demo_unrealized_pnl(
    db: AsyncSession, subscription_id: int, mids: dict[str, str]
) -> float:
    open_res = await db.execute(
        select(UserTrade)
        .where(
            UserTrade.subscription_id == subscription_id,
            UserTrade.trade_type == "open",
            UserTrade.is_demo.is_(True),
            UserTrade.status == "filled",
        )
        .order_by(UserTrade.executed_at.asc())
    )
    open_trades = open_res.scalars().all()
    if not open_trades:
        return 0.0

    close_res = await db.execute(
        select(UserTrade.coin, func.max(UserTrade.executed_at))
        .where(
            UserTrade.subscription_id == subscription_id,
            UserTrade.trade_type == "close",
            UserTrade.is_demo.is_(True),
        )
        .group_by(UserTrade.coin)
    )
    last_close_by_coin: dict[str | None, datetime] = {
        row[0]: row[1] for row in close_res.all()
    }

    total_unrealized = 0.0
    seen: set[str | None] = set()
    for trade in reversed(open_trades):  # most recent first
        if trade.coin in seen:
            continue
        seen.add(trade.coin)
        last_close = last_close_by_coin.get(trade.coin)
        if last_close is not None and last_close >= trade.executed_at:
            continue  # position has been closed
        mid_str = mids.get(trade.coin or "")
        if mid_str is None or trade.price is None or trade.size is None:
            continue
        direction = 1.0 if trade.side == "long" else -1.0
        total_unrealized += (
            (float(mid_str) - float(trade.price)) * float(trade.size) * direction
        )

    return total_unrealized


async def _to_response(
    db: AsyncSession,
    sub: Subscription,
    mids: dict[str, str] | None = None,
) -> SubscriptionResponse:
    if sub.is_demo:
        pnl_result = await db.execute(
            select(
                func.coalesce(func.sum(UserTrade.realized_pnl), Decimal("0")),
                func.count(UserTrade.id),
            ).where(
                UserTrade.subscription_id == sub.id,
                UserTrade.trade_type == "close",
                UserTrade.status == "filled",
                UserTrade.is_demo.is_(True),
            )
        )
    else:
        pnl_result = await db.execute(
            select(
                func.coalesce(func.sum(UserTrade.price * UserTrade.size), Decimal("0")),
                func.count(UserTrade.id),
            ).where(
                UserTrade.subscription_id == sub.id,
                UserTrade.status == "filled",
            )
        )
    pnl_row = pnl_result.one()
    realized_pnl = float(pnl_row[0]) if pnl_row[0] else 0.0
    trade_count = int(pnl_row[1])

    unrealized_pnl = 0.0
    if sub.is_demo and mids:
        unrealized_pnl = await _compute_demo_unrealized_pnl(db, sub.id, mids)

    trader_res = await db.execute(
        select(Trader.hl_address, Trader.display_name).where(Trader.id == sub.trader_id)
    )
    trader_row = trader_res.one_or_none()

    return SubscriptionResponse(
        id=sub.id,
        trader_id=sub.trader_id,
        trader_address=trader_row[0] if trader_row else None,
        trader_name=trader_row[1] if trader_row else None,
        max_allocation_usd=float(sub.max_allocation_usd),
        copy_ratio_pct=float(sub.copy_ratio_pct),
        stop_loss_pct=float(sub.stop_loss_pct),
        max_leverage=float(sub.max_leverage),
        sizing_mode=sub.sizing_mode,
        max_per_coin_usd=(
            float(sub.max_per_coin_usd) if sub.max_per_coin_usd is not None else None
        ),
        allowed_coins=(
            list(sub.allowed_coins) if sub.allowed_coins is not None else None
        ),
        source_type=sub.source_type,
        source_id=sub.source_id,
        source_version_id=sub.source_version_id,
        managed_by_portfolio=sub.managed_by_portfolio,
        is_active=sub.is_active,
        is_demo=sub.is_demo,
        created_at=sub.created_at,
        realized_pnl=realized_pnl,
        unrealized_pnl=unrealized_pnl,
        trade_count=trade_count,
    )


async def create_subscription(
    db: AsyncSession,
    user_id: int,
    data: SubscriptionCreate,
    user_hl_address: str | None,
    *,
    source_type: str = "manual",
    source_id: int | None = None,
    source_version_id: int | None = None,
    managed_by_portfolio: bool = False,
    margin_summary: MarginSummary | None = None,
) -> SubscriptionResponse:
    trader_res = await db.execute(
        select(Trader).where(Trader.id == data.trader_id, Trader.is_active.is_(True))
    )
    trader = trader_res.scalar_one_or_none()
    if trader is None:
        raise ValueError(f"Trader {data.trader_id} not found or inactive")

    if not data.is_demo:
        if not user_hl_address:
            raise ValueError("HL wallet address required to create a subscription")

        if margin_summary is None:
            try:
                hl = HyperliquidInfoClient()
                margin_summary = await hl.get_account_summary(user_hl_address)
            except Exception as exc:
                logger.error("subscription_equity_fetch_failed", error=str(exc))
                raise ValueError(
                    "Failed to fetch HL account data — try again later"
                ) from exc

        allowed, reason = await check_portfolio_risk(
            db,
            user_id,
            data.max_allocation_usd,
            float(data.max_leverage),
            margin_summary,
        )
        if not allowed:
            raise ValueError(reason)

    sub = Subscription(
        user_id=user_id,
        trader_id=data.trader_id,
        max_allocation_usd=data.max_allocation_usd,
        copy_ratio_pct=data.copy_ratio_pct,
        stop_loss_pct=data.stop_loss_pct,
        max_leverage=data.max_leverage,
        sizing_mode=data.sizing_mode,
        max_per_coin_usd=data.max_per_coin_usd,
        allowed_coins=data.allowed_coins,
        source_type=source_type,
        source_id=source_id,
        source_version_id=source_version_id,
        managed_by_portfolio=managed_by_portfolio,
        is_active=True,
        is_demo=data.is_demo,
    )
    db.add(sub)
    await db.flush()
    return await _to_response(db, sub)


async def list_subscriptions(
    db: AsyncSession, user_id: int, is_demo: bool = False
) -> list[SubscriptionResponse]:
    result = await db.execute(
        select(Subscription).where(
            Subscription.user_id == user_id,
            Subscription.is_active.is_(True),
            Subscription.is_demo.is_(is_demo),
        )
    )
    subs = result.scalars().all()

    mids: dict[str, str] | None = None
    if is_demo and subs:
        try:
            hl = HyperliquidInfoClient()
            mids = await hl.get_all_mids()
        except Exception as exc:
            logger.warning("demo_mids_fetch_failed", error=str(exc))

    return [await _to_response(db, s, mids) for s in subs]


async def update_subscription(
    db: AsyncSession, user_id: int, subscription_id: int, data: SubscriptionUpdate
) -> SubscriptionResponse:
    sub = await _get_owned(db, user_id, subscription_id)

    if data.max_allocation_usd is not None:
        sub.max_allocation_usd = data.max_allocation_usd
    if data.copy_ratio_pct is not None:
        sub.copy_ratio_pct = data.copy_ratio_pct
    if data.stop_loss_pct is not None:
        sub.stop_loss_pct = data.stop_loss_pct
    if data.max_leverage is not None:
        sub.max_leverage = data.max_leverage
    if data.sizing_mode is not None:
        sub.sizing_mode = data.sizing_mode
    if "max_per_coin_usd" in data.model_fields_set:
        sub.max_per_coin_usd = data.max_per_coin_usd
    if "allowed_coins" in data.model_fields_set:
        sub.allowed_coins = data.allowed_coins

    return await _to_response(db, sub)


async def delete_subscription(
    db: AsyncSession, user_id: int, subscription_id: int, close_positions: bool = True
) -> None:
    sub = await _get_owned(db, user_id, subscription_id)

    if sub.is_demo:
        from app.services.demo_service import (  # noqa: PLC0415
            close_demo_subscription_positions,
        )

        await close_demo_subscription_positions(db, sub)
        sub.is_active = False
        return

    sub.is_active = False

    if close_positions:
        from app.tasks.execution_tasks import (
            close_subscription_positions_async,  # noqa: PLC0415
        )

        asyncio.create_task(
            close_subscription_positions_async(user_id, subscription_id)
        )


async def _get_owned(
    db: AsyncSession, user_id: int, subscription_id: int
) -> Subscription:
    result = await db.execute(
        select(Subscription).where(
            Subscription.id == subscription_id,
            Subscription.user_id == user_id,
        )
    )
    sub = result.scalar_one_or_none()
    if sub is None:
        raise LookupError(f"Subscription {subscription_id} not found")
    return sub
