"""Job Router handling background job scheduling and priority queue telemetry."""

from typing import Annotated
from fastapi import APIRouter, Depends, status

from app.schemas.job import (
    JobScheduleRequest,
    JobScheduleResponse,
    JobStatusResponse,
)
from app.services.job_service import JobService, get_job_service

router = APIRouter(prefix="/jobs", tags=["Jobs"])


@router.post(
    "/schedule",
    response_model=JobScheduleResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Schedule a priority-based background job",
)
async def schedule_job(
    request: JobScheduleRequest,
    service: Annotated[JobService, Depends(get_job_service)],
) -> JobScheduleResponse:
    """Schedule a task into the binary min-heap priority queue with O(log N) complexity."""
    result = await service.schedule_job(
        task_type=request.task_type,
        payload=request.payload,
        priority=request.priority,
        delay_seconds=request.delay_seconds,
    )
    return JobScheduleResponse.model_validate(result)


@router.get(
    "/status",
    response_model=JobStatusResponse,
    status_code=status.HTTP_200_OK,
    summary="Get priority queue size and next pending job telemetry",
)
async def get_job_queue_status(
    service: Annotated[JobService, Depends(get_job_service)],
) -> JobStatusResponse:
    """Inspect the binary min-heap root job and queue depth in O(1) time."""
    telemetry = await service.get_telemetry()
    return JobStatusResponse.model_validate(telemetry)


__all__ = [
    "router",
]
