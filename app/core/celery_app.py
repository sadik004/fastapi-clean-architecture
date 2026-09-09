"""Celery distributed task queue and periodic scheduling configuration module."""

from celery import Celery
from celery.schedules import crontab

from app.core.config import get_settings

settings = get_settings()

celery_app = Celery(
    "fastapi_clean_architecture",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=[
        "app.tasks.report_tasks",
        "app.tasks.scheduled_tasks",
    ],
)

# Production Configuration Hardening & Beat Periodic Scheduling
celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_time_limit=300,  # 5 minutes hard limit
    task_soft_time_limit=240,  # 4 minutes graceful warning
    worker_prefetch_multiplier=1,  # fair dispatch, prevents worker task starvation
    task_acks_late=True,  # guarantees task is re-queued if worker process crashes mid-execution
    result_expires=86400,  # results expire after 24 hours
    beat_schedule_filename="celerybeat-schedule",
    beat_schedule={
        "nightly-reconciliation-audit": {
            "task": "app.tasks.scheduled_tasks.nightly_reconciliation_audit",
            "schedule": crontab(hour=0, minute=0),  # Runs daily at 00:00 UTC
            "options": {"queue": "celery"},
        },
        "prune-expired-sessions-and-tokens": {
            "task": "app.tasks.scheduled_tasks.prune_expired_sessions_and_tokens",
            "schedule": crontab(minute=0),  # Runs hourly at minute 0
            "options": {"queue": "celery"},
        },
        "system-health-heartbeat": {
            "task": "app.tasks.scheduled_tasks.system_health_heartbeat",
            "schedule": 60.0,  # Runs every 60 seconds
            "options": {"queue": "celery"},
        },
    },
)
