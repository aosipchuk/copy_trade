from celery import Celery
from celery.schedules import crontab

from app.core.config import settings

celery_app = Celery(
    "copy_trade",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    include=[
        "app.tasks.hl_tracker",
        "app.tasks.signal_consumer",
        "app.tasks.execution_tasks",
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
            "schedule": 300.0,  # every 5 minutes
        },
        "monitor-pending-trades": {
            "task": "app.tasks.execution_tasks.monitor_pending_trades",
            "schedule": 30.0,  # every 30 seconds
        },
    },
)
