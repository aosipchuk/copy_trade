import asyncio
from datetime import datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_task_db_session
from app.core.logging import get_logger
from app.models.subscription import Subscription
from app.models.trade import UserTrade
from app.models.trader import Trader
from app.services.hyperliquid.info_client import HyperliquidInfoClient
from app.tasks.celery_app import celery_app

logger = get_logger(__name__)


async def _load_stuck_positions(
    db: AsyncSession,
) -> tuple[dict[tuple[int, str | None], UserTrade], dict[int, str]]:
    """
    Return (truly_open, sub_to_address) where truly_open contains the most
    recent open demo trade per (subscription_id, coin) that has no close after it.
    """
    subs_result = await db.execute(
        select(Subscription).where(
            Subscription.is_demo.is_(True),
            Subscription.is_active.is_(True),
        )
    )
    subs = subs_result.scalars().all()
    if not subs:
        return {}, {}

    sub_ids = [s.id for s in subs]
    sub_map = {s.id: s for s in subs}

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
    if not all_open_trades:
        return {}, {}

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

    truly_open: dict[tuple[int, str | None], UserTrade] = {}
    for trade in reversed(all_open_trades):
        key = (trade.subscription_id, trade.coin)
        if key in truly_open:
            continue
        last_close = last_close_by_sub_coin.get(key)
        if last_close is not None and last_close >= trade.executed_at:
            continue
        truly_open[key] = trade

    if not truly_open:
        return {}, {}

    involved_sub_ids = list({sub_id for sub_id, _ in truly_open})
    trader_ids = list(
        {sub_map[sid].trader_id for sid in involved_sub_ids if sid in sub_map}
    )
    trader_result = await db.execute(
        select(Trader.id, Trader.hl_address).where(Trader.id.in_(trader_ids))
    )
    trader_address_by_id: dict[int, str] = {
        row[0]: row[1] for row in trader_result.all()
    }
    sub_to_address: dict[int, str] = {
        sid: trader_address_by_id[sub_map[sid].trader_id]
        for sid in involved_sub_ids
        if sid in sub_map and sub_map[sid].trader_id in trader_address_by_id
    }

    return truly_open, sub_to_address


async def _fetch_live_positions(
    addresses: list[str],
    hl: HyperliquidInfoClient,
) -> dict[str, set[tuple[str, str]] | None]:
    """Fetch current HL positions for all addresses concurrently."""
    results = await asyncio.gather(
        *[hl.get_positions(addr) for addr in addresses],
        return_exceptions=True,
    )
    live: dict[str, set[tuple[str, str]] | None] = {}
    for address, result in zip(addresses, results, strict=True):
        if isinstance(result, BaseException):
            logger.warning(
                "reconcile_fetch_failed", trader=address, error=str(result)
            )
            live[address] = None
        else:
            live[address] = {(p.coin, p.side) for p in result}
    return live


def _build_to_close(
    truly_open: dict[tuple[int, str | None], UserTrade],
    sub_to_address: dict[int, str],
    live_positions: dict[str, set[tuple[str, str]] | None],
) -> list[tuple[int, str, UserTrade]]:
    """Return (sub_id, coin, open_trade) tuples for positions absent on HL."""
    to_close: list[tuple[int, str, UserTrade]] = []
    for (sub_id, coin), open_trade in truly_open.items():
        if coin is None:
            continue
        trader_address: str | None = sub_to_address.get(sub_id)
        if trader_address is None:
            continue
        live = live_positions.get(trader_address)
        if live is None:
            continue
        if (coin, open_trade.side or "long") not in live:
            to_close.append((sub_id, coin, open_trade))
    return to_close


async def _write_close_trades(
    db: AsyncSession,
    to_close: list[tuple[int, str, UserTrade]],
    mids: dict[str, str],
) -> int:
    """Persist synthetic close trades; returns the number of trades written."""
    closed_count = 0
    for sub_id, coin, open_trade in to_close:
        mid_str = mids.get(coin)
        if mid_str is None:
            logger.debug("reconcile_no_mid", coin=coin)
            continue
        if open_trade.price is None or open_trade.size is None:
            logger.warning(
                "reconcile_skip_null_fields",
                trade_id=open_trade.id,
                coin=coin,
            )
            continue

        close_price = Decimal(mid_str)
        entry_price = Decimal(str(open_trade.price))
        size = Decimal(str(open_trade.size))
        side = open_trade.side or "long"
        direction = Decimal("1") if side == "long" else Decimal("-1")
        realized_pnl = (close_price - entry_price) * size * direction

        db.add(
            UserTrade(
                subscription_id=sub_id,
                signal_id=open_trade.signal_id,
                coin=coin,
                side=side,
                size=float(size),
                price=float(close_price),
                status="filled",
                trade_type="close",
                realized_pnl=float(realized_pnl),
                is_demo=True,
            )
        )
        closed_count += 1
        logger.info(
            "demo_reconcile_close_synthesized",
            subscription_id=sub_id,
            coin=coin,
            side=side,
            entry_price=float(entry_price),
            close_price=float(close_price),
            realized_pnl=float(realized_pnl),
        )
    return closed_count


async def _reconcile_async() -> int:
    """
    Find demo open trades with no subsequent close whose position no longer
    exists on Hyperliquid, then synthesize a close trade at the current mid price.

    Returns the number of positions closed.
    """
    async with get_task_db_session() as db:
        truly_open, sub_to_address = await _load_stuck_positions(db)

    if not truly_open:
        return 0

    hl = HyperliquidInfoClient()
    unique_addresses = list(set(sub_to_address.values()))
    live_positions = await _fetch_live_positions(unique_addresses, hl)

    to_close = _build_to_close(truly_open, sub_to_address, live_positions)
    if not to_close:
        logger.debug("demo_reconcile_nothing_to_close")
        return 0

    try:
        mids = await hl.get_all_mids()
    except Exception as exc:
        logger.warning("reconcile_mids_fetch_failed", error=str(exc))
        return 0

    async with get_task_db_session() as db:
        count = await _write_close_trades(db, to_close, mids)

    logger.info("demo_reconcile_complete", closed=count)
    return count


@celery_app.task(  # type: ignore[untyped-decorator]
    name="app.tasks.demo_reconcile.reconcile_demo_positions",
    bind=True,
    max_retries=3,
)
def reconcile_demo_positions(self) -> None:  # type: ignore[no-untyped-def]
    """Synthesize close trades for demo positions that vanished from Hyperliquid."""
    try:
        count = asyncio.run(_reconcile_async())
        if count:
            logger.info("demo_positions_reconciled", count=count)
    except Exception as exc:
        logger.error("demo_reconcile_task_failed", error=str(exc))
        raise self.retry(exc=exc, countdown=60) from exc
