from app.core.logging import get_logger
from app.tasks.celery_app import celery_app

logger = get_logger(__name__)


@celery_app.task(name="app.tasks.hl_tracker.refresh_leaderboard", bind=True, max_retries=3)
def refresh_leaderboard(self) -> None:  # type: ignore[no-untyped-def]
    """Fetch top traders from Hyperliquid leaderboard and update DB."""
    logger.info("refresh_leaderboard_started")


@celery_app.task(name="app.tasks.hl_tracker.track_active_traders", bind=True, max_retries=3)
def track_active_traders(self) -> None:  # type: ignore[no-untyped-def]
    """Poll positions for all traders that have active subscribers."""
    logger.info("track_active_traders_started")


@celery_app.task(name="app.tasks.hl_tracker.poll_trader_positions", bind=True, max_retries=3)
def poll_trader_positions(self, trader_address: str) -> None:  # type: ignore[no-untyped-def]
    """Snapshot positions for a single trader and detect signal changes."""
    logger.info("poll_trader_positions_started", trader=trader_address)
