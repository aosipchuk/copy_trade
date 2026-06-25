import asyncio
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.orm import aliased

from app.core.config import settings
from app.core.database import get_db_session
from app.core.logging import get_logger
from app.models.trader import Trader, TraderStat
from app.services.analytics.metrics import (
    compute_composite_score,
    compute_trader_quality_metrics,
)
from app.services.hyperliquid.rate_limiter import hl_priority_low

logger = get_logger(__name__)

_BATCH_SIZE = 10  # coroutines in flight per inner batch
_CHUNK_SIZE = 300  # traders per priority chunk (ordered by 30d ROI)
_CHUNK_PAUSE_SEC = 5.0  # breather between chunks; rate limiter governs real pace


async def _compute_and_save(
    trader_id: int, hl_address: str, roi_30d_pct: float | None
) -> bool:
    # Mark this task's HL calls as background so the rate limiter prioritizes
    # real-time polling and user-facing requests over analytics.
    hl_priority_low.set(True)
    try:
        metrics = await compute_trader_quality_metrics(hl_address)
        if metrics is None:
            return False

        metrics.composite_score = compute_composite_score(
            roi_30d_pct=roi_30d_pct,
            win_rate_pct=metrics.win_rate_pct,
            sharpe_ratio=metrics.sharpe_ratio,
            sortino_ratio=metrics.sortino_ratio,
            max_drawdown_pct=metrics.max_drawdown_pct,
            profit_factor=metrics.profit_factor,
            profitable_days_pct=metrics.profitable_days_pct,
            max_losing_streak=metrics.max_losing_streak,
            avg_trades_per_day=metrics.avg_trades_per_day,
        )

        now = datetime.now(UTC).replace(tzinfo=None)
        values = {**metrics.to_dict(), "updated_at": now}

        async with get_db_session() as db:
            await db.execute(
                update(TraderStat)
                .where(TraderStat.trader_id == trader_id)
                .values(**values)
            )

        logger.debug("quality_metrics_saved", trader=hl_address)
        return True
    except Exception as exc:
        logger.warning("quality_metrics_failed", trader=hl_address, error=str(exc))
        return False


async def _compute_quality_metrics_async() -> int:
    trader_stat_month = aliased(TraderStat)
    async with get_db_session() as db:
        result = await db.execute(
            select(Trader.id, Trader.hl_address, trader_stat_month.roi_pct)
            .outerjoin(
                trader_stat_month,
                (trader_stat_month.trader_id == Trader.id)
                & (trader_stat_month.period == "month"),
            )
            .where(Trader.is_active == True)  # noqa: E712
            # Highest 30-day ROI first: the traders users are most likely to view
            # get fresh metrics earliest each cycle. NULL ROI processed last.
            .order_by(trader_stat_month.roi_pct.desc().nulls_last())
        )
        traders = result.all()

    # Bound the dominant background HL load: only the top-N by 30d ROI get fresh
    # metrics each cycle. Log the drop so the cap is never silent.
    total_active = len(traders)
    cap = settings.hl_quality_metrics_max_traders
    if cap > 0 and total_active > cap:
        traders = traders[:cap]
        logger.info("quality_metrics_capped", cap=cap, dropped=total_active - cap)

    logger.info("quality_metrics_started", total=len(traders))
    processed = 0
    # Walk the ROI-ordered list in priority chunks of _CHUNK_SIZE. The global HL
    # rate limiter caps the real API rate; the chunk pause adds breathing room and
    # marks progress for the most-viewed traders first.
    for chunk_start in range(0, len(traders), _CHUNK_SIZE):
        chunk = traders[chunk_start : chunk_start + _CHUNK_SIZE]
        for i in range(0, len(chunk), _BATCH_SIZE):
            batch = chunk[i : i + _BATCH_SIZE]
            results = await asyncio.gather(
                *[
                    _compute_and_save(
                        tid, addr, float(roi) if roi is not None else None
                    )
                    for tid, addr, roi in batch
                ],
                return_exceptions=True,
            )
            processed += sum(1 for r in results if r is True)

        logger.info(
            "quality_metrics_chunk_done",
            done=min(chunk_start + _CHUNK_SIZE, len(traders)),
            total=len(traders),
            processed=processed,
        )
        if chunk_start + _CHUNK_SIZE < len(traders):
            await asyncio.sleep(_CHUNK_PAUSE_SEC)

    return processed


async def compute_quality_metrics_async() -> None:
    """Compute quality metrics and composite score for all active traders."""
    try:
        count = await _compute_quality_metrics_async()
        logger.info("quality_metrics_computed", processed=count)
    except Exception as exc:
        logger.error("quality_metrics_task_failed", error=str(exc))
