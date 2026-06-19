import asyncio
from datetime import UTC, datetime

from pydantic import TypeAdapter
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.sql import func

from app.core.clickhouse_client import get_ch_client
from app.core.database import get_task_db_session
from app.core.logging import get_logger
from app.core.redis_client import get_redis_client
from app.models.subscription import Subscription
from app.models.trader import Trader, TraderStat
from app.services.hyperliquid.info_client import HyperliquidInfoClient
from app.services.hyperliquid.models import Position
from app.services.signal_detector import SignalEvent, detect_changes
from app.services.signal_publisher import save_signals
from app.tasks.celery_app import celery_app

logger = get_logger(__name__)

_LEADERBOARD_TOP_N = 500
_SNAPSHOT_TTL = 60  # seconds in Redis
_position_adapter: TypeAdapter[list[Position]] = TypeAdapter(list[Position])


# ─── Helpers ──────────────────────────────────────────────────────────────────


def _utcnow() -> datetime:
    return datetime.now(tz=UTC).replace(tzinfo=None)


def _serialize_positions(positions: list[Position]) -> str:
    return _position_adapter.dump_json(positions).decode()


def _deserialize_positions(raw: str) -> list[Position]:
    return _position_adapter.validate_json(raw)


def _write_positions_to_clickhouse(address: str, positions: list[Position]) -> None:
    now = _utcnow()
    rows = [
        (
            address,
            p.coin,
            p.side,
            float(p.szi),
            float(p.entry_px) if p.entry_px is not None else 0.0,
            float(p.unrealized_pnl),
            float(p.leverage.value),
            now,
        )
        for p in positions
    ]
    ch = get_ch_client()
    ch.execute(
        "INSERT INTO copytrade.trader_positions"
        " (trader_address, coin, side, szi,"
        " entry_px, unrealized_pnl, leverage, snapshot_at)"
        " VALUES",
        rows,
    )


# ─── Async business logic ─────────────────────────────────────────────────────


async def _refresh_leaderboard_async() -> int:
    client = HyperliquidInfoClient()
    rows = await client.get_leaderboard()
    rows = rows[:_LEADERBOARD_TOP_N]

    now = _utcnow()
    ch = get_ch_client()

    async with get_task_db_session() as db:
        for row in rows:
            stmt = (
                pg_insert(Trader)
                .values(
                    hl_address=row.eth_address,
                    display_name=row.display_name,
                    is_active=True,
                    last_seen_at=now,
                )
                .on_conflict_do_update(
                    index_elements=["hl_address"],
                    set_={
                        "display_name": row.display_name,
                        "last_seen_at": now,
                        "is_active": True,
                    },
                )
                .returning(Trader.id)
            )
            result = await db.execute(stmt)
            trader_id: int = result.scalar_one()

            for period, perf in row.window_performances:
                stat_stmt = (
                    pg_insert(TraderStat)
                    .values(
                        trader_id=trader_id,
                        period=period,
                        pnl_usd=float(perf.pnl),
                        roi_pct=float(perf.roi),
                        volume_usd=float(perf.vlm),
                    )
                    .on_conflict_do_update(
                        constraint="trader_stats_pkey",
                        set_={
                            "pnl_usd": float(perf.pnl),
                            "roi_pct": float(perf.roi),
                            "volume_usd": float(perf.vlm),
                            "updated_at": func.now(),
                        },
                    )
                )
                await db.execute(stat_stmt)

                # ClickHouse: append PnL history point
                try:
                    ch.execute(
                        "INSERT INTO copytrade.trader_pnl"
                        " (trader_address, ts, pnl, roi, period) VALUES",
                        [
                            (
                                row.eth_address,
                                now,
                                float(perf.pnl),
                                float(perf.roi),
                                period,
                            )
                        ],
                    )
                except Exception as ch_err:
                    logger.warning(
                        "ch_pnl_write_failed", trader=row.eth_address, error=str(ch_err)
                    )

    return len(rows)


async def _get_tracked_addresses() -> list[str]:
    async with get_task_db_session() as db:
        result = await db.execute(
            select(Trader.hl_address)
            .join(Subscription, Subscription.trader_id == Trader.id)
            .where(
                Subscription.is_active == True,  # noqa: E712
                Trader.is_active == True,  # noqa: E712
            )
            .distinct()
        )
        return list(result.scalars().all())


async def _poll_trader_positions_async(trader_address: str) -> int:
    """
    1. Fetch current positions from Hyperliquid.
    2. Compare with previous snapshot stored in Redis.
    3. Write snapshot to ClickHouse.
    4. Persist detected signals to PostgreSQL and dispatch fan-out tasks.

    Returns number of signals detected.
    """
    client = HyperliquidInfoClient()
    curr_positions = await client.get_positions(trader_address)

    redis_cli = get_redis_client()
    snap_key = f"hl:snapshot:{trader_address}"

    prev_raw: str | None = redis_cli.get(snap_key)
    prev_positions = _deserialize_positions(prev_raw) if prev_raw else []

    # Persist current snapshot
    redis_cli.setex(snap_key, _SNAPSHOT_TTL, _serialize_positions(curr_positions))

    # ClickHouse — write position snapshot (best-effort)
    if curr_positions:
        try:
            _write_positions_to_clickhouse(trader_address, curr_positions)
        except Exception as ch_err:
            logger.warning(
                "ch_positions_write_failed", trader=trader_address, error=str(ch_err)
            )

    if not prev_positions:
        return 0  # first snapshot — no baseline to compare

    events: list[SignalEvent] = detect_changes(prev_positions, curr_positions)
    if not events:
        return 0

    async with get_task_db_session() as db:
        result = await db.execute(
            select(Trader).where(Trader.hl_address == trader_address)
        )
        trader: Trader | None = result.scalar_one_or_none()
        if trader is None:
            logger.warning("poll_trader_not_in_db", address=trader_address)
            return 0

        signal_ids = await save_signals(db, trader.id, trader_address, events)

    # Dispatch fan-out tasks outside the DB transaction
    from app.tasks.signal_consumer import (
        fan_out_signal,  # deferred to avoid circular import
    )

    for sig_id in signal_ids:
        fan_out_signal.delay(sig_id)

    logger.info(
        "signals_dispatched",
        count=len(signal_ids),
        trader=trader_address,
    )
    return len(signal_ids)


# ─── Celery tasks ─────────────────────────────────────────────────────────────


@celery_app.task(  # type: ignore[untyped-decorator]
    name="app.tasks.hl_tracker.refresh_leaderboard", bind=True, max_retries=3
)
def refresh_leaderboard(self) -> None:  # type: ignore[no-untyped-def]
    """Fetch top-500 traders from Hyperliquid leaderboard and upsert to DB."""
    try:
        count = asyncio.run(_refresh_leaderboard_async())
        logger.info("leaderboard_refreshed", upserted=count)
    except Exception as exc:
        logger.error("leaderboard_refresh_failed", error=str(exc))
        raise self.retry(exc=exc, countdown=30) from exc


@celery_app.task(  # type: ignore[untyped-decorator]
    name="app.tasks.hl_tracker.track_active_traders", bind=True, max_retries=3
)
def track_active_traders(self) -> None:  # type: ignore[no-untyped-def]
    """Dispatch position-polling tasks for traders with active subscribers."""
    try:
        addresses = asyncio.run(_get_tracked_addresses())
        for address in addresses:
            poll_trader_positions.delay(address)
        if addresses:
            logger.debug("tracking_dispatched", count=len(addresses))
    except Exception as exc:
        logger.error("track_active_traders_failed", error=str(exc))
        raise self.retry(exc=exc, countdown=5) from exc


@celery_app.task(  # type: ignore[untyped-decorator]
    name="app.tasks.hl_tracker.poll_trader_positions", bind=True, max_retries=3
)
def poll_trader_positions(self, trader_address: str) -> None:  # type: ignore[no-untyped-def]
    """Snapshot positions for a single trader and detect signal changes."""
    try:
        n = asyncio.run(_poll_trader_positions_async(trader_address))
        if n:
            logger.info("positions_polled", signals=n, trader=trader_address)
    except Exception as exc:
        logger.error("poll_positions_failed", trader=trader_address, error=str(exc))
        raise self.retry(exc=exc, countdown=10) from exc
