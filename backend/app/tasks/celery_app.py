from celery import Celery

from app.core.config import settings

celery_app = Celery(
    "copy_trade",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    include=[
        "app.tasks.hl_tracker",
        "app.tasks.signal_consumer",
        "app.tasks.execution_tasks",
        "app.tasks.analytics_tasks",
        "app.tasks.demo_reconcile",
    ],
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    task_routes={
        "app.tasks.hl_tracker.*": {"queue": "default"},
        "app.tasks.signal_consumer.*": {"queue": "signals"},
        "app.tasks.execution_tasks.*": {"queue": "execution"},
        "app.tasks.analytics_tasks.*": {"queue": "default"},
    },
    beat_schedule={
        "refresh-leaderboard": {
            "task": "app.tasks.hl_tracker.refresh_leaderboard",
            "schedule": 600.0,  # every 10 minutes
        },
        "track-active-traders": {
            "task": "app.tasks.hl_tracker.track_active_traders",
            "schedule": 5.0,  # every 5 seconds
        },
        "check-stop-losses": {
            "task": "app.tasks.execution_tasks.check_stop_losses",
            "schedule": 60.0,  # every 60 s; 30 s Redis margin cache limits HL API load
        },
        "monitor-pending-trades": {
            "task": "app.tasks.execution_tasks.monitor_pending_trades",
            "schedule": 30.0,  # every 30 seconds
        },
        "compute-quality-metrics": {
            "task": "app.tasks.analytics_tasks.compute_quality_metrics",
            "schedule": 10800.0,  # every 3 hours
        },
        "refresh-human-scores": {
            "task": "app.tasks.hl_tracker.refresh_human_scores",
            "schedule": 14400.0,  # every 4 hours
        },
        "reconcile-demo-positions": {
            "task": "app.tasks.demo_reconcile.reconcile_demo_positions",
            "schedule": 300.0,  # every 5 minutes
        },
    },
)
