"""Service layer for inspecting and manually triggering periodic tasks."""

from __future__ import annotations

from typing import Any

from celery.schedules import crontab

from app.core.celery_app import celery_app
from app.core.exceptions import EntityNotFoundException
from app.schemas.schedule import ScheduleEntryResponse, ScheduleListResponse
from app.tasks.scheduled_tasks import (
    nightly_reconciliation_audit,
    prune_expired_sessions_and_tokens,
    system_health_heartbeat,
)

# O(1) Hash Map for fast task lookup by name, key, or alias
_TASK_REGISTRY: dict[str, Any] = {
    "nightly-reconciliation-audit": nightly_reconciliation_audit,
    "nightly_reconciliation_audit": nightly_reconciliation_audit,
    "nightly-audit": nightly_reconciliation_audit,
    "app.tasks.scheduled_tasks.nightly_reconciliation_audit": nightly_reconciliation_audit,
    "prune-expired-sessions-and-tokens": prune_expired_sessions_and_tokens,
    "prune_expired_sessions_and_tokens": prune_expired_sessions_and_tokens,
    "prune-sessions": prune_expired_sessions_and_tokens,
    "app.tasks.scheduled_tasks.prune_expired_sessions_and_tokens": prune_expired_sessions_and_tokens,
    "system-health-heartbeat": system_health_heartbeat,
    "system_health_heartbeat": system_health_heartbeat,
    "heartbeat": system_health_heartbeat,
    "app.tasks.scheduled_tasks.system_health_heartbeat": system_health_heartbeat,
}


class ScheduleService:
    """Encapsulates business operations for periodic task scheduling."""

    @staticmethod
    def get_active_schedules() -> ScheduleListResponse:
        """Inspect and return all periodic task schedules registered in Celery Beat."""
        beat_schedule = getattr(celery_app.conf, "beat_schedule", {})
        entries: list[ScheduleEntryResponse] = []

        for name, config in beat_schedule.items():
            schedule_val = config.get("schedule")
            if isinstance(schedule_val, crontab):
                sched_type = "crontab"
                sched_expr = str(schedule_val)
            else:
                sched_type = "interval"
                sched_expr = f"every {schedule_val}s"

            queue = config.get("options", {}).get("queue", "celery")
            task_path = config.get("task", "")

            entries.append(
                ScheduleEntryResponse(
                    name=name,
                    task=task_path,
                    schedule_type=sched_type,
                    schedule_expression=sched_expr,
                    queue=queue,
                )
            )

        return ScheduleListResponse(
            total_schedules=len(entries),
            schedules=entries,
        )

    @staticmethod
    def trigger_scheduled_task_manually(task_name: str) -> str:
        """Trigger an ad-hoc run of a registered periodic task immediately.

        Args:
            task_name: Key, alias, or full path of the periodic task.

        Returns:
            task_id: Unique string identifier of the dispatched Celery task.

        Raises:
            EntityNotFoundException: If task_name is not registered in the catalog.
        """
        task_fn = _TASK_REGISTRY.get(task_name)
        if not task_fn:
            raise EntityNotFoundException(
                message=f"Scheduled task '{task_name}' is not registered in the periodic catalog.",
                code="SCHEDULED_TASK_NOT_FOUND",
            )

        async_result = task_fn.delay()
        return str(async_result.id)
