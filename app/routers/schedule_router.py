"""Schedule management router exposing periodic schedule inspection and manual triggers."""

from __future__ import annotations

from fastapi import APIRouter, status

from app.schemas.schedule import ManualTriggerResponse, ScheduleListResponse
from app.services.schedule_service import ScheduleService

router = APIRouter(prefix="/schedules", tags=["Schedules"])


@router.get(
    "",
    response_model=ScheduleListResponse,
    summary="List all configured Celery Beat periodic task schedules",
)
def get_schedules() -> ScheduleListResponse:
    """Retrieve all periodic tasks and their configured cron or interval expressions."""
    return ScheduleService.get_active_schedules()


@router.post(
    "/trigger/{task_name}",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=ManualTriggerResponse,
    summary="Manually trigger an ad-hoc run of a registered periodic task",
)
def trigger_scheduled_task(task_name: str) -> ManualTriggerResponse:
    """Manually dispatch a registered periodic task into the Celery task queue.

    Returns HTTP 202 Accepted with a correlation task_id for tracking progress.
    """
    task_id = ScheduleService.trigger_scheduled_task_manually(task_name=task_name)
    return ManualTriggerResponse(
        task_name=task_name,
        task_id=task_id,
        status="PENDING",
        message=f"Scheduled task '{task_name}' dispatched successfully.",
    )
