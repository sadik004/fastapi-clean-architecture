"""Celery distributed task queue configuration module."""

from celery import Celery

from app.core.config import get_settings

settings = get_settings()

celery_app = Celery(
    "fastapi_clean_architecture",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=["app.tasks.report_tasks"],
)

# Production Configuration Hardening
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
)
