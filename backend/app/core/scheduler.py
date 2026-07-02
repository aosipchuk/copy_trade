from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from app.core.logging import get_logger

logger = get_logger(__name__)

scheduler = AsyncIOScheduler(timezone="UTC")


def setup_scheduler() -> None:
    """Register all periodic jobs. Called once from FastAPI lifespan."""
    from app.tasks.analytics_tasks import compute_quality_metrics_async
    from app.tasks.demo_reconcile import reconcile_async
    from app.tasks.execution_tasks import (
        check_stop_losses_async,
        monitor_pending_trades_async,
    )
    from app.tasks.hl_tracker import (
        refresh_human_scores_async,
        refresh_leaderboard_async,
        track_active_traders_async,
    )
    from app.tasks.portfolio_tasks import (
        apply_due_user_rebalances_async,
        generate_weekly_portfolio_reports_async,
    )

    scheduler.add_job(
        refresh_leaderboard_async,
        IntervalTrigger(seconds=600),
        id="refresh_leaderboard",
        replace_existing=True,
        max_instances=1,
    )
    scheduler.add_job(
        track_active_traders_async,
        IntervalTrigger(seconds=5),
        id="track_active_traders",
        replace_existing=True,
        max_instances=1,
    )
    scheduler.add_job(
        check_stop_losses_async,
        IntervalTrigger(seconds=60),
        id="check_stop_losses",
        replace_existing=True,
        max_instances=1,
    )
    scheduler.add_job(
        monitor_pending_trades_async,
        IntervalTrigger(seconds=30),
        id="monitor_pending_trades",
        replace_existing=True,
        max_instances=1,
    )
    scheduler.add_job(
        compute_quality_metrics_async,
        IntervalTrigger(seconds=3600),
        id="compute_quality_metrics",
        replace_existing=True,
        max_instances=1,
    )
    scheduler.add_job(
        refresh_human_scores_async,
        IntervalTrigger(seconds=14400),
        id="refresh_human_scores",
        replace_existing=True,
        max_instances=1,
    )
    scheduler.add_job(
        reconcile_async,
        IntervalTrigger(seconds=300),
        id="reconcile_demo_positions",
        replace_existing=True,
        max_instances=1,
    )
    scheduler.add_job(
        apply_due_user_rebalances_async,
        IntervalTrigger(seconds=300),
        id="apply_due_user_rebalances",
        replace_existing=True,
        max_instances=1,
    )
    scheduler.add_job(
        generate_weekly_portfolio_reports_async,
        IntervalTrigger(seconds=86400),
        id="generate_weekly_portfolio_reports",
        replace_existing=True,
        max_instances=1,
    )
    logger.info("scheduler_jobs_registered", count=len(scheduler.get_jobs()))
