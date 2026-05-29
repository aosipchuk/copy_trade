from app.core.logging import get_logger
from app.tasks.celery_app import celery_app

logger = get_logger(__name__)


@celery_app.task(name="app.tasks.signal_consumer.fan_out_signal", bind=True, max_retries=3)
def fan_out_signal(self, signal_id: int) -> None:  # type: ignore[no-untyped-def]
    """Find all active subscribers for the signal's trader and dispatch execution tasks."""
    logger.info("fan_out_signal_started", signal_id=signal_id)
