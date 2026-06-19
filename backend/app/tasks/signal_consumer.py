import asyncio

from sqlalchemy import select

from app.core.database import get_task_db_session
from app.core.logging import get_logger
from app.models.subscription import Subscription
from app.tasks.celery_app import celery_app

logger = get_logger(__name__)


async def _get_active_subscriber_ids(signal_id: int) -> dict[str, list[int]]:
    """Return {'real': [...user_ids], 'demo': [...user_ids]} for this signal."""
    async with get_task_db_session() as db:
        from app.models.signal import Signal

        result = await db.execute(select(Signal).where(Signal.id == signal_id))
        signal = result.scalar_one_or_none()
        if signal is None:
            logger.warning("fan_out_signal_not_found", signal_id=signal_id)
            return {"real": [], "demo": []}

        sub_result = await db.execute(
            select(Subscription.user_id, Subscription.is_demo).where(
                Subscription.trader_id == signal.trader_id,
                Subscription.is_active == True,  # noqa: E712
            )
        )
        rows = sub_result.all()

    real = [uid for uid, is_demo in rows if not is_demo]
    demo = [uid for uid, is_demo in rows if is_demo]
    return {"real": real, "demo": demo}


@celery_app.task(  # type: ignore[untyped-decorator]
    name="app.tasks.signal_consumer.fan_out_signal", bind=True, max_retries=3
)
def fan_out_signal(self, signal_id: int) -> None:  # type: ignore[no-untyped-def]
    """Find all active subscribers for signal's trader and dispatch execution tasks."""
    try:
        ids = asyncio.run(_get_active_subscriber_ids(signal_id))

        from app.tasks.execution_tasks import execute_copy_trade, simulate_demo_trade

        for user_id in ids["real"]:
            execute_copy_trade.delay(signal_id, user_id)
        for user_id in ids["demo"]:
            simulate_demo_trade.delay(signal_id, user_id)

        logger.info(
            "fan_out_dispatched",
            signal_id=signal_id,
            real=len(ids["real"]),
            demo=len(ids["demo"]),
        )
    except Exception as exc:
        logger.error("fan_out_failed", signal_id=signal_id, error=str(exc))
        raise self.retry(exc=exc, countdown=5) from exc
