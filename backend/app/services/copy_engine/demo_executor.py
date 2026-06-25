import asyncio
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db_session
from app.core.logging import get_logger
from app.core.redis_client import get_redis_client
from app.models.signal import Signal
from app.models.subscription import Subscription
from app.models.trade import UserTrade
from app.services.copy_engine.order_builder import signal_to_order
from app.services.hyperliquid.info_client import HyperliquidInfoClient
from app.services.hyperliquid.models import Meta

logger = get_logger(__name__)

DEMO_DEDUP_TTL: int = 3600


def _demo_dedup_key(signal_id: int, user_id: int) -> str:
    return f"demo:dedup:{signal_id}:{user_id}"


async def simulate_demo_trade(signal_id: int, user_id: int) -> None:
    """Paper-trade simulation: fetch mid price and record a virtual UserTrade."""
    r = get_redis_client()
    key = _demo_dedup_key(signal_id, user_id)
    if r.exists(key):
        logger.info("demo_trade_dedup_skip", signal_id=signal_id, user_id=user_id)
        return
    r.setex(key, DEMO_DEDUP_TTL, "1")

    async with get_db_session() as db:
        signal, subscription = await _load_demo_context(db, signal_id, user_id)
        if signal is None or subscription is None:
            return

        hl = HyperliquidInfoClient()
        mids, meta = await asyncio.gather(hl.get_all_mids(), hl.get_meta())

        mid_str = mids.get(signal.coin)
        if mid_str is None:
            logger.debug("demo_trade_no_mid_price", coin=signal.coin)
            return
        mid_price = Decimal(mid_str)

        if signal.signal_type == "CLOSE":
            await _handle_demo_close(db, signal, subscription, mid_price)
        else:
            await _handle_demo_open(db, signal, subscription, mid_price, meta)


async def _load_demo_context(
    db: AsyncSession, signal_id: int, user_id: int
) -> tuple[Signal | None, Subscription | None]:
    signal_res = await db.execute(select(Signal).where(Signal.id == signal_id))
    signal = signal_res.scalar_one_or_none()
    if signal is None:
        logger.warning("demo_executor_signal_not_found", signal_id=signal_id)
        return None, None

    sub_res = await db.execute(
        select(Subscription).where(
            Subscription.trader_id == signal.trader_id,
            Subscription.user_id == user_id,
            Subscription.is_active == True,  # noqa: E712
            Subscription.is_demo == True,  # noqa: E712
        )
    )
    subscription = sub_res.scalar_one_or_none()
    if subscription is None:
        logger.debug(
            "demo_executor_no_active_demo_subscription",
            user_id=user_id,
            signal_id=signal_id,
        )
    return signal, subscription


async def _handle_demo_open(
    db: AsyncSession,
    signal: Signal,
    subscription: Subscription,
    mid_price: Decimal,
    meta: Meta,
) -> None:
    asset_meta_obj = next((a for a in meta.universe if a.name == signal.coin), None)
    if asset_meta_obj is None:
        logger.debug("demo_open_coin_not_in_meta", coin=signal.coin)
        return

    virtual_equity = Decimal(str(subscription.max_allocation_usd))
    order = signal_to_order(
        signal=signal,
        subscription=subscription,
        user_equity=virtual_equity,
        mid_price=mid_price,
        asset_meta=asset_meta_obj,
    )
    if order is None:
        logger.debug("demo_open_order_skipped", signal_id=signal.id)
        return

    side = "long" if order.is_buy else "short"
    db.add(
        UserTrade(
            subscription_id=subscription.id,
            signal_id=signal.id,
            coin=signal.coin,
            side=side,
            size=float(order.size),
            price=float(mid_price),
            status="filled",
            trade_type="open",
            is_demo=True,
        )
    )
    logger.info(
        "demo_trade_open_saved",
        signal_id=signal.id,
        coin=signal.coin,
        side=side,
        size=float(order.size),
        price=float(mid_price),
    )


async def _handle_demo_close(
    db: AsyncSession,
    signal: Signal,
    subscription: Subscription,
    mid_price: Decimal,
) -> None:
    open_trade = await _find_open_demo_trade(db, subscription.id, signal.coin)
    if open_trade is None:
        logger.debug(
            "demo_close_no_open_trade",
            subscription_id=subscription.id,
            coin=signal.coin,
        )
        return

    entry_price = Decimal(str(open_trade.price))
    size = Decimal(str(open_trade.size))
    direction = Decimal("1") if open_trade.side == "long" else Decimal("-1")
    realized_pnl = (mid_price - entry_price) * size * direction

    db.add(
        UserTrade(
            subscription_id=subscription.id,
            signal_id=signal.id,
            coin=signal.coin,
            side=open_trade.side,
            size=float(size),
            price=float(mid_price),
            status="filled",
            trade_type="close",
            realized_pnl=float(realized_pnl),
            is_demo=True,
        )
    )
    logger.info(
        "demo_trade_close_saved",
        signal_id=signal.id,
        coin=signal.coin,
        realized_pnl=float(realized_pnl),
    )


async def _find_open_demo_trade(
    db: AsyncSession, subscription_id: int, coin: str
) -> UserTrade | None:
    result = await db.execute(
        select(UserTrade)
        .where(
            UserTrade.subscription_id == subscription_id,
            UserTrade.coin == coin,
            UserTrade.trade_type == "open",
            UserTrade.is_demo == True,  # noqa: E712
        )
        .order_by(UserTrade.executed_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()
