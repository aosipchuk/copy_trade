import asyncio
from datetime import UTC, datetime

from sqlalchemy import select, update

from app.core.database import get_task_db_session
from app.core.logging import get_logger
from app.models.trader import Trader, TraderStat
from app.services.analytics.metrics import compute_trader_quality_metrics
from app.tasks.celery_app import celery_app

logger = get_logger(__name__)

_TOP_N_QUALITY = 200
_BATCH_SIZE = 20
_BATCH_PAUSE_SEC = 2.0


async def _compute_and_save(trader_id: int, hl_address: str) -> bool:
    try:
        metrics = await compute_trader_quality_metrics(hl_address)
        if metrics is None:
            return False

        now = datetime.now(UTC).replace(tzinfo=None)
        values = {**metrics.to_dict(), "updated_at": now}

        async with get_task_db_session() as db:
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
    async with get_task_db_session() as db:
        result = await db.execute(
            select(Trader.id, Trader.hl_address)
            .join(TraderStat, TraderStat.trader_id == Trader.id)
            .where(
                Trader.is_active == True,  # noqa: E712
                TraderStat.period == "week",
                TraderStat.roi_pct.isnot(None),
            )
            .order_by(TraderStat.roi_pct.desc().nulls_last())
            .limit(_TOP_N_QUALITY)
        )
        traders = result.all()

    processed = 0
    for i in range(0, len(traders), _BATCH_SIZE):
        batch = traders[i : i + _BATCH_SIZE]
        results = await asyncio.gather(
            *[_compute_and_save(tid, addr) for tid, addr in batch],
            return_exceptions=True,
        )
        processed += sum(1 for r in results if r is True)

        if i + _BATCH_SIZE < len(traders):
            await asyncio.sleep(_BATCH_PAUSE_SEC)

    return processed


@celery_app.task(  # type: ignore[untyped-decorator]
    name="app.tasks.analytics_tasks.compute_quality_metrics",
    bind=True,
    max_retries=2,
)
def compute_quality_metrics(self) -> None:  # type: ignore[no-untyped-def]
    """Compute quality metrics (win rate, drawdown, trade count) for top-200 traders."""
    try:
        count = asyncio.run(_compute_quality_metrics_async())
        logger.info("quality_metrics_computed", processed=count)
    except Exception as exc:
        logger.error("quality_metrics_task_failed", error=str(exc))
        raise self.retry(exc=exc, countdown=300) from exc
