import asyncio
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.orm import aliased

from app.core.database import get_db_session
from app.core.logging import get_logger
from app.models.trader import Trader, TraderStat
from app.services.analytics.metrics import (
    compute_composite_score,
    compute_trader_quality_metrics,
)

logger = get_logger(__name__)

_BATCH_SIZE = 10
_BATCH_PAUSE_SEC = 5.0


async def _compute_and_save(
    trader_id: int, hl_address: str, roi_30d_pct: float | None
) -> bool:
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
        )
        traders = result.all()

    logger.info("quality_metrics_started", total=len(traders))
    processed = 0
    for i in range(0, len(traders), _BATCH_SIZE):
        batch = traders[i : i + _BATCH_SIZE]
        results = await asyncio.gather(
            *[
                _compute_and_save(tid, addr, float(roi) if roi is not None else None)
                for tid, addr, roi in batch
            ],
            return_exceptions=True,
        )
        processed += sum(1 for r in results if r is True)

        if i + _BATCH_SIZE < len(traders):
            await asyncio.sleep(_BATCH_PAUSE_SEC)

    return processed


async def compute_quality_metrics_async() -> None:
    """Compute quality metrics and composite score for all active traders."""
    try:
        count = await _compute_quality_metrics_async()
        logger.info("quality_metrics_computed", processed=count)
    except Exception as exc:
        logger.error("quality_metrics_task_failed", error=str(exc))
