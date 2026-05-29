from app.core.logging import get_logger
from app.tasks.celery_app import celery_app

logger = get_logger(__name__)


@celery_app.task(name="app.tasks.execution_tasks.execute_copy_trade", bind=True, max_retries=3, default_retry_delay=5)
def execute_copy_trade(self, signal_id: int, user_id: int) -> None:  # type: ignore[no-untyped-def]
    """Execute a copy trade for a specific user based on a signal."""
    logger.info("execute_copy_trade_started", signal_id=signal_id, user_id=user_id)


@celery_app.task(name="app.tasks.execution_tasks.check_stop_losses")
def check_stop_losses() -> None:
    """Check all active subscriptions and deactivate those that hit stop-loss."""
    logger.info("check_stop_losses_started")


@celery_app.task(name="app.tasks.execution_tasks.monitor_pending_trades")
def monitor_pending_trades() -> None:
    """Update status of pending trades by checking Hyperliquid order status."""
    logger.info("monitor_pending_trades_started")
