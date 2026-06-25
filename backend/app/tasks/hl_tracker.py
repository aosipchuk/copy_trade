import asyncio
from datetime import UTC, datetime
from decimal import Decimal

from pydantic import TypeAdapter
from sqlalchemy import case, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.sql import func

from app.core.config import settings
from app.core.database import get_db_session
from app.core.logging import get_logger
from app.core.redis_client import get_redis_client
from app.models.subscription import Subscription
from app.models.trader import Trader, TraderStat
from app.services.hydromancer.client import HydromancerClient
from app.services.hyperliquid.info_client import HyperliquidInfoClient
from app.services.hyperliquid.models import Position
from app.services.signal_detector import SignalEvent, detect_changes
from app.services.signal_publisher import save_signals

logger = get_logger(__name__)

_MIN_30D_ROI = Decimal("0.03")  # Level 1: minimum 30-day ROI (3%)
_MIN_ACCOUNT_VALUE_USD = 1_000  # skip empty / abandoned accounts
_MAX_TRADERS = 5_000            # safety cap against unexpected leaderboard growth
_SNAPSHOT_TTL = 60              # seconds in Redis
_position_adapter: TypeAdapter[list[Position]] = TypeAdapter(list[Position])

# Limit concurrent HL HTTP requests to avoid memory spikes in a single uvicorn process.
# With Celery, each poll ran in a separate worker; now all run in the same asyncio loop.
_POLL_SEMAPHORE = asyncio.Semaphore(10)


# ─── Helpers ──────────────────────────────────────────────────────────────────


def _utcnow() -> datetime:
    return datetime.now(tz=UTC).replace(tzinfo=None)


def _serialize_positions(positions: list[Position]) -> str:
    return _position_adapter.dump_json(positions).decode()


def _deserialize_positions(raw: str) -> list[Position]:
    return _position_adapter.validate_json(raw)


# ─── Async business logic ─────────────────────────────────────────────────────


def _filter_leaderboard(all_rows: list) -> list:  # type: ignore[type-arg]
    """Level-1 gate: keep traders with positive 30-day ROI and non-empty account."""
    filtered = [
        row
        for row in all_rows
        if (month := row.get_perf("month")) is not None
        and month.roi > _MIN_30D_ROI
        and row.account_value >= _MIN_ACCOUNT_VALUE_USD
    ]
    return filtered[:_MAX_TRADERS]


async def refresh_leaderboard_async() -> int:
    client = HyperliquidInfoClient()
    all_rows = await client.get_leaderboard()
    rows = _filter_leaderboard(all_rows)

    logger.info(
        "leaderboard_filtered",
        total=len(all_rows),
        passed=len(rows),
    )

    now = _utcnow()

    async with get_db_session() as db:
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

        # Deactivate traders that no longer pass the leaderboard filter.
        # Skip traders that have active subscriptions so users aren't surprised.
        active_addresses = [row.eth_address for row in rows]
        subscribed_ids = select(Subscription.trader_id).where(
            Subscription.is_active == True  # noqa: E712
        )
        deactivated = await db.execute(
            update(Trader)
            .where(
                Trader.hl_address.not_in(active_addresses),
                Trader.is_active == True,  # noqa: E712
                Trader.id.not_in(subscribed_ids),
            )
            .values(is_active=False)
            .returning(Trader.id)
        )
        deactivated_count = len(deactivated.fetchall())
        if deactivated_count:
            logger.info("leaderboard_deactivated", count=deactivated_count)

    return len(rows)


async def _get_tracked_addresses() -> list[str]:
    async with get_db_session() as db:
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
    3. Persist detected signals to PostgreSQL and fan out trades.

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

    if not prev_positions:
        return 0  # first snapshot — no baseline to compare

    events: list[SignalEvent] = detect_changes(prev_positions, curr_positions)
    if not events:
        return 0

    async with get_db_session() as db:
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
        fan_out_signal_async,  # deferred to avoid circular import
    )

    for sig_id in signal_ids:
        await fan_out_signal_async(sig_id)

    logger.info(
        "signals_dispatched",
        count=len(signal_ids),
        trader=trader_address,
    )
    return len(signal_ids)


async def _poll_and_execute(trader_address: str) -> None:
    """Poll one trader and fan-out copy trades for all subscribers."""
    async with _POLL_SEMAPHORE:
        try:
            await _poll_trader_positions_async(trader_address)
        except Exception as exc:
            logger.error("poll_positions_failed", trader=trader_address, error=str(exc))


async def track_active_traders_async() -> None:
    """Poll positions for all traders with active subscribers in parallel."""
    addresses = await _get_tracked_addresses()
    if not addresses:
        return
    results = await asyncio.gather(
        *[_poll_and_execute(addr) for addr in addresses],
        return_exceptions=True,
    )
    failed = sum(1 for r in results if isinstance(r, Exception))
    logger.debug("tracking_dispatched", count=len(addresses), failed=failed)


async def refresh_human_scores_async() -> int:
    """Fetch human scores from Hydromancer and update the traders table.

    Only updates rows that already exist in our DB — does not insert new traders.
    Traders absent from Hydromancer's response keep their previous score.
    """
    if not settings.hydromancer_api_key:
        logger.info("hydromancer_skipped", reason="HYDROMANCER_API_KEY not set")
        return 0

    client = HydromancerClient(settings.hydromancer_api_key)
    users = await client.get_human_scores(window="all", min_human_score=0)

    if not users:
        return 0

    score_map: dict[str, int] = {u.user.lower(): u.human_score for u in users}

    to_update: list[tuple[int, int]] = []
    async with get_db_session() as db:
        result = await db.execute(select(Trader.id, Trader.hl_address))
        rows = result.all()
        to_update = [
            (tid, score_map[addr.lower()])
            for tid, addr in rows
            if addr.lower() in score_map
        ]
        if to_update:
            ids = [tid for tid, _ in to_update]
            await db.execute(
                update(Trader)
                .where(Trader.id.in_(ids))
                .values(
                    human_score=case(
                        {tid: score for tid, score in to_update},
                        value=Trader.id,
                    )
                )
            )
    updated = len(to_update)

    logger.info("human_scores_updated", updated=updated, total=len(users))
    return updated
