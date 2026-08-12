import asyncio
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy import func, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.copy_execution import CopyPositionTarget
from app.models.portfolio import UserPortfolioSubscription
from app.models.subscription import Subscription
from app.models.trade import UserTrade
from app.models.trader import Trader
from app.models.user import User
from app.schemas.subscription import (
    SubscriptionCreate,
    SubscriptionResponse,
    SubscriptionUpdate,
)
from app.services.hyperliquid.info_client import HyperliquidInfoClient
from app.services.hyperliquid.models import MarginSummary
from app.services.portfolio.access import user_can_view_subscription_trader_identity
from app.services.portfolio.billing import (
    PAID_BILLING_STATUSES,
    user_has_beta_override,
)
from app.services.risk_manager import check_portfolio_risk

logger = get_logger(__name__)


@dataclass(frozen=True)
class _SubscriptionTradeStats:
    realized_pnl: float = 0.0
    unrealized_pnl: float = 0.0
    trade_count: int = 0


_DEFAULT_TRADE_STATS = _SubscriptionTradeStats()
_TraderIdentity = tuple[str | None, str | None]


def _response_from_parts(
    sub: Subscription,
    stats: _SubscriptionTradeStats,
    trader_identity: _TraderIdentity | None,
    *,
    include_trader_identity: bool = True,
) -> SubscriptionResponse:
    trader_address, trader_name = trader_identity or (None, None)

    return SubscriptionResponse(
        id=sub.id,
        trader_id=sub.trader_id if include_trader_identity else None,
        trader_address=trader_address if include_trader_identity else None,
        trader_name=trader_name if include_trader_identity else None,
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
        expires_at=sub.expires_at,
        ended_reason=sub.ended_reason,
        engine_version=sub.engine_version,
        execution_status=sub.execution_status,
        pause_reason=sub.pause_reason,
        execution_status_details=sub.execution_status_details,
        resumed_at=sub.resumed_at,
        blocked_at=sub.blocked_at,
        created_at=sub.created_at,
        realized_pnl=stats.realized_pnl,
        unrealized_pnl=stats.unrealized_pnl,
        trade_count=stats.trade_count,
    )


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


async def _compute_demo_unrealized_pnl_by_subscription(
    db: AsyncSession, subscription_ids: list[int]
) -> dict[int, float]:
    if not subscription_ids:
        return {}

    open_res = await db.execute(
        select(UserTrade)
        .where(
            UserTrade.subscription_id.in_(subscription_ids),
            UserTrade.trade_type == "open",
            UserTrade.is_demo.is_(True),
            UserTrade.status == "filled",
        )
        .order_by(UserTrade.executed_at.asc())
    )
    open_trades = open_res.scalars().all()
    if not open_trades:
        return {}

    close_res = await db.execute(
        select(
            UserTrade.subscription_id,
            UserTrade.coin,
            func.max(UserTrade.executed_at),
        )
        .where(
            UserTrade.subscription_id.in_(subscription_ids),
            UserTrade.trade_type == "close",
            UserTrade.is_demo.is_(True),
        )
        .group_by(UserTrade.subscription_id, UserTrade.coin)
    )
    last_close_by_sub_coin: dict[tuple[int, str | None], datetime] = {
        (row[0], row[1]): row[2] for row in close_res.all()
    }

    latest_open_trades: list[UserTrade] = []
    seen: set[tuple[int, str | None]] = set()
    for trade in reversed(open_trades):
        key = (trade.subscription_id, trade.coin)
        if key in seen:
            continue
        seen.add(key)

        last_close = last_close_by_sub_coin.get(key)
        if last_close is not None and last_close >= trade.executed_at:
            continue

        latest_open_trades.append(trade)

    if not latest_open_trades:
        return {}

    try:
        hl = HyperliquidInfoClient()
        mids = await hl.get_all_mids()
    except Exception as exc:
        logger.warning("demo_mids_fetch_failed", error=str(exc))
        return {}

    total_by_sub_id: dict[int, float] = {}
    for trade in latest_open_trades:
        mid_str = mids.get(trade.coin or "")
        if mid_str is None or trade.price is None or trade.size is None:
            continue

        direction = 1.0 if trade.side == "long" else -1.0
        total_by_sub_id[trade.subscription_id] = (
            total_by_sub_id.get(
                trade.subscription_id,
                0.0,
            )
            + (float(mid_str) - float(trade.price)) * float(trade.size) * direction
        )

    return total_by_sub_id


async def _load_subscription_trade_stats(
    db: AsyncSession,
    subscription_ids: list[int],
    *,
    is_demo: bool,
) -> dict[int, _SubscriptionTradeStats]:
    if not subscription_ids:
        return {}

    stats_by_sub_id: dict[int, _SubscriptionTradeStats] = dict.fromkeys(
        subscription_ids, _DEFAULT_TRADE_STATS
    )

    if is_demo:
        pnl_result = await db.execute(
            select(
                UserTrade.subscription_id,
                func.coalesce(func.sum(UserTrade.realized_pnl), Decimal("0")),
                func.count(UserTrade.id),
            )
            .where(
                UserTrade.subscription_id.in_(subscription_ids),
                UserTrade.trade_type == "close",
                UserTrade.status == "filled",
                UserTrade.is_demo.is_(True),
            )
            .group_by(UserTrade.subscription_id)
        )
    else:
        pnl_result = await db.execute(
            select(
                UserTrade.subscription_id,
                func.coalesce(func.sum(UserTrade.price * UserTrade.size), Decimal("0")),
                func.count(UserTrade.id),
            )
            .where(
                UserTrade.subscription_id.in_(subscription_ids),
                UserTrade.status == "filled",
            )
            .group_by(UserTrade.subscription_id)
        )

    for subscription_id, realized_pnl, trade_count in pnl_result.all():
        stats_by_sub_id[subscription_id] = _SubscriptionTradeStats(
            realized_pnl=float(realized_pnl) if realized_pnl else 0.0,
            trade_count=int(trade_count),
        )

    if is_demo:
        unrealized_by_sub_id = await _compute_demo_unrealized_pnl_by_subscription(
            db,
            subscription_ids,
        )
        for subscription_id, unrealized_pnl in unrealized_by_sub_id.items():
            current = stats_by_sub_id.get(subscription_id, _DEFAULT_TRADE_STATS)
            stats_by_sub_id[subscription_id] = _SubscriptionTradeStats(
                realized_pnl=current.realized_pnl,
                unrealized_pnl=unrealized_pnl,
                trade_count=current.trade_count,
            )

    return stats_by_sub_id


async def _load_subscription_trader_visibility(
    db: AsyncSession,
    user_id: int,
    subscriptions: list[Subscription],
) -> dict[int, bool]:
    visibility_by_sub_id = {sub.id: True for sub in subscriptions}
    gated_subscriptions = [
        sub
        for sub in subscriptions
        if sub.source_type == "model_portfolio"
        and sub.managed_by_portfolio
        and sub.source_id is not None
    ]
    if not gated_subscriptions:
        return visibility_by_sub_id

    user = await db.get(User, user_id)
    if user is not None and user_has_beta_override(user):
        return visibility_by_sub_id

    source_ids = sorted(
        {sub.source_id for sub in gated_subscriptions if sub.source_id is not None}
    )
    source_result = await db.execute(
        select(
            UserPortfolioSubscription.id,
            UserPortfolioSubscription.portfolio_id,
            UserPortfolioSubscription.active_version_id,
        ).where(
            UserPortfolioSubscription.id.in_(source_ids),
            UserPortfolioSubscription.user_id == user_id,
        )
    )
    source_by_id: dict[int, tuple[int, int]] = {
        row[0]: (row[1], row[2]) for row in source_result.all()
    }

    paid_portfolio_versions: set[tuple[int, int]] = set()
    portfolio_versions = sorted(set(source_by_id.values()))
    if portfolio_versions:
        paid_result = await db.execute(
            select(
                UserPortfolioSubscription.portfolio_id,
                UserPortfolioSubscription.active_version_id,
            ).where(
                UserPortfolioSubscription.user_id == user_id,
                UserPortfolioSubscription.is_demo.is_(False),
                UserPortfolioSubscription.status.in_(PAID_BILLING_STATUSES),
                tuple_(
                    UserPortfolioSubscription.portfolio_id,
                    UserPortfolioSubscription.active_version_id,
                ).in_(portfolio_versions),
            )
        )
        paid_portfolio_versions = {(row[0], row[1]) for row in paid_result.all()}

    for sub in gated_subscriptions:
        source_id = sub.source_id
        if source_id is None:
            continue
        portfolio_version = source_by_id.get(source_id)
        visibility_by_sub_id[sub.id] = (
            portfolio_version in paid_portfolio_versions
            if portfolio_version is not None
            else False
        )

    return visibility_by_sub_id


async def _to_response(
    db: AsyncSession,
    sub: Subscription,
    mids: dict[str, str] | None = None,
    *,
    include_trader_identity: bool = True,
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
    trader_identity = (trader_row[0], trader_row[1]) if trader_row else None

    return _response_from_parts(
        sub,
        _SubscriptionTradeStats(
            realized_pnl=realized_pnl,
            unrealized_pnl=unrealized_pnl,
            trade_count=trade_count,
        ),
        trader_identity,
        include_trader_identity=include_trader_identity,
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
    expires_at: datetime | None = None,
) -> SubscriptionResponse:
    trader_query = select(Trader).where(
        Trader.id == data.trader_id,
        Trader.is_active.is_(True),
    )
    if source_type == "new_wallet":
        trader_query = trader_query.where(Trader.has_perp_activity.is_not(False))
    else:
        trader_query = trader_query.where(Trader.has_perp_activity.is_(True))

    trader_res = await db.execute(trader_query)
    trader = trader_res.scalar_one_or_none()
    if trader is None:
        raise ValueError(f"Trader {data.trader_id} not found or not copyable")

    if not data.is_demo:
        if not user_hl_address:
            raise ValueError("HL wallet address required to create a subscription")

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
        is_active=data.is_demo,
        is_demo=data.is_demo,
        expires_at=expires_at,
        engine_version=2,
        execution_status="active" if data.is_demo else "paused",
        pause_reason=None if data.is_demo else "preflight_required",
    )
    db.add(sub)
    await db.flush()
    if data.is_demo:
        from app.services.copy_engine.resume import initialize_subscription_baseline

        await initialize_subscription_baseline(db, sub, trader)
    return await _to_response(db, sub)


async def list_subscriptions(
    db: AsyncSession,
    user_id: int,
    is_demo: bool = False,
    include_inactive: bool = False,
) -> list[SubscriptionResponse]:
    filters = [
        Subscription.user_id == user_id,
        Subscription.is_demo.is_(is_demo),
    ]
    if not include_inactive:
        filters.append(Subscription.is_active.is_(True))

    result = await db.execute(
        select(Subscription)
        .where(*filters)
        .order_by(Subscription.created_at.desc(), Subscription.id.desc())
    )
    subs = list(result.scalars().all())
    sub_ids = [sub.id for sub in subs]

    stats_by_sub_id = await _load_subscription_trade_stats(
        db,
        sub_ids,
        is_demo=is_demo,
    )
    visible_identity_by_sub_id = await _load_subscription_trader_visibility(
        db,
        user_id,
        subs,
    )

    visible_trader_ids = sorted(
        {sub.trader_id for sub in subs if visible_identity_by_sub_id.get(sub.id, True)}
    )
    trader_identity_by_id: dict[int, _TraderIdentity] = {}
    if visible_trader_ids:
        trader_result = await db.execute(
            select(Trader.id, Trader.hl_address, Trader.display_name).where(
                Trader.id.in_(visible_trader_ids)
            )
        )
        trader_identity_by_id = {
            row[0]: (row[1], row[2]) for row in trader_result.all()
        }

    return [
        _response_from_parts(
            sub,
            stats_by_sub_id.get(sub.id, _DEFAULT_TRADE_STATS),
            trader_identity_by_id.get(sub.trader_id),
            include_trader_identity=visible_identity_by_sub_id.get(sub.id, True),
        )
        for sub in subs
    ]


async def get_subscription(
    db: AsyncSession,
    user_id: int,
    subscription_id: int,
) -> SubscriptionResponse:
    sub = await _get_owned(db, user_id, subscription_id)
    return await _to_response(
        db,
        sub,
        include_trader_identity=await user_can_view_subscription_trader_identity(
            db,
            user_id,
            sub,
        ),
    )


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

    if not sub.is_demo and sub.execution_status == "active":
        sub.is_active = False
        sub.execution_status = "paused"
        sub.pause_reason = "settings_changed_preflight_required"

    return await _to_response(
        db,
        sub,
        include_trader_identity=await user_can_view_subscription_trader_identity(
            db,
            user_id,
            sub,
        ),
    )


async def delete_subscription(
    db: AsyncSession, user_id: int, subscription_id: int, close_positions: bool = True
) -> None:
    sub = await _get_owned(db, user_id, subscription_id)

    from app.services.copy_engine.lifecycle import stop_subscription_targets

    if not close_positions and not sub.is_demo:
        target_result = await db.execute(
            select(CopyPositionTarget.id).where(
                CopyPositionTarget.subscription_id == sub.id,
                CopyPositionTarget.target_size != 0,
            )
        )
        if target_result.first() is not None:
            raise ValueError(
                "A live subscription with non-zero targets cannot be detached"
            )
    await stop_subscription_targets(db, sub, reason="user_requested_stop")


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
