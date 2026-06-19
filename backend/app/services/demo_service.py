from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.subscription import Subscription
from app.models.trade import UserTrade
from app.models.trader import Trader
from app.schemas.subscription import DemoOpenPosition, DemoPortfolioResponse, DemoTradeItem
from app.services.hyperliquid.info_client import HyperliquidInfoClient

logger = get_logger(__name__)


async def get_demo_portfolio(
    db: AsyncSession, user_id: int
) -> DemoPortfolioResponse:
    subs_result = await db.execute(
        select(Subscription).where(
            Subscription.user_id == user_id,
            Subscription.is_demo.is_(True),
            Subscription.is_active.is_(True),
        )
    )
    subs = subs_result.scalars().all()
    sub_ids = [s.id for s in subs]

    if not sub_ids:
        return DemoPortfolioResponse(
            total_realized_pnl=0.0,
            total_unrealized_pnl=0.0,
            trade_count=0,
            win_count=0,
            win_rate_pct=0.0,
            open_positions=[],
        )

    pnl_result = await db.execute(
        select(
            func.coalesce(func.sum(UserTrade.realized_pnl), 0.0),
            func.count(UserTrade.id),
            func.count(UserTrade.id).filter(UserTrade.realized_pnl > 0),
        ).where(
            UserTrade.subscription_id.in_(sub_ids),
            UserTrade.trade_type == "close",
            UserTrade.is_demo.is_(True),
            UserTrade.status == "filled",
        )
    )
    pnl_row = pnl_result.one()
    total_realized_pnl = float(pnl_row[0]) if pnl_row[0] else 0.0
    trade_count = int(pnl_row[1])
    win_count = int(pnl_row[2])

    open_result = await db.execute(
        select(UserTrade)
        .where(
            UserTrade.subscription_id.in_(sub_ids),
            UserTrade.trade_type == "open",
            UserTrade.is_demo.is_(True),
            UserTrade.status == "filled",
        )
        .order_by(UserTrade.executed_at.asc())
    )
    all_open_trades = open_result.scalars().all()

    close_result = await db.execute(
        select(
            UserTrade.subscription_id,
            UserTrade.coin,
            func.max(UserTrade.executed_at),
        )
        .where(
            UserTrade.subscription_id.in_(sub_ids),
            UserTrade.trade_type == "close",
            UserTrade.is_demo.is_(True),
        )
        .group_by(UserTrade.subscription_id, UserTrade.coin)
    )
    last_close_by_sub_coin: dict[tuple[int, str | None], datetime] = {
        (row[0], row[1]): row[2] for row in close_result.all()
    }

    # For each (subscription, coin) keep only the most recent open trade
    # that has no close trade after it.
    truly_open: dict[tuple[int, str | None], UserTrade] = {}
    for trade in reversed(all_open_trades):
        key = (trade.subscription_id, trade.coin)
        if key in truly_open:
            continue
        last_close = last_close_by_sub_coin.get(key)
        if last_close is not None and last_close >= trade.executed_at:
            continue
        truly_open[key] = trade

    mids: dict[str, str] = {}
    if truly_open:
        try:
            hl = HyperliquidInfoClient()
            mids = await hl.get_all_mids()
        except Exception as exc:
            logger.warning("demo_portfolio_mids_fetch_failed", error=str(exc))

    sub_map = {s.id: s for s in subs}
    trader_ids = list({s.trader_id for s in subs})
    trader_result = await db.execute(
        select(Trader.id, Trader.display_name).where(Trader.id.in_(trader_ids))
    )
    trader_name_by_id: dict[int, str | None] = {
        row[0]: row[1] for row in trader_result.all()
    }

    open_positions: list[DemoOpenPosition] = []
    total_unrealized_pnl = 0.0
    for (sub_id, coin), trade in truly_open.items():
        current_mid_str = mids.get(coin or "")
        if current_mid_str is None or trade.price is None or trade.size is None:
            continue
        current_price = float(current_mid_str)
        entry_price = float(trade.price)
        size = float(trade.size)
        direction = 1.0 if trade.side == "long" else -1.0
        unrealized_pnl = (current_price - entry_price) * size * direction
        total_unrealized_pnl += unrealized_pnl

        sub = sub_map.get(sub_id)
        trader_name = trader_name_by_id.get(sub.trader_id) if sub else None

        open_positions.append(
            DemoOpenPosition(
                subscription_id=sub_id,
                trader_name=trader_name,
                coin=coin or "",
                side=trade.side or "",
                size=size,
                entry_price=entry_price,
                current_price=current_price,
                unrealized_pnl=unrealized_pnl,
            )
        )

    win_rate_pct = (win_count / trade_count * 100) if trade_count > 0 else 0.0

    return DemoPortfolioResponse(
        total_realized_pnl=total_realized_pnl,
        total_unrealized_pnl=total_unrealized_pnl,
        trade_count=trade_count,
        win_count=win_count,
        win_rate_pct=win_rate_pct,
        open_positions=open_positions,
    )


async def get_demo_subscription_trades(
    db: AsyncSession, user_id: int, subscription_id: int, limit: int = 100
) -> list[DemoTradeItem]:
    sub_result = await db.execute(
        select(Subscription).where(
            Subscription.id == subscription_id,
            Subscription.user_id == user_id,
            Subscription.is_demo.is_(True),
        )
    )
    if sub_result.scalar_one_or_none() is None:
        return []

    trades_result = await db.execute(
        select(UserTrade)
        .where(
            UserTrade.subscription_id == subscription_id,
            UserTrade.is_demo.is_(True),
            UserTrade.status == "filled",
        )
        .order_by(UserTrade.executed_at.desc())
        .limit(limit)
    )
    trades = trades_result.scalars().all()

    return [
        DemoTradeItem(
            id=t.id,
            coin=t.coin or "",
            side=t.side or "",
            size=float(t.size) if t.size is not None else 0.0,
            price=float(t.price) if t.price is not None else 0.0,
            trade_type=t.trade_type or "open",
            realized_pnl=float(t.realized_pnl) if t.realized_pnl is not None else None,
            executed_at=t.executed_at,
        )
        for t in trades
    ]
