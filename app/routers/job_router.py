"""Job Router handling background job scheduling and priority queue telemetry."""

from typing import Annotated

from fastapi import APIRouter, Depends, Path, status

from app.schemas.job import (
    ExclusiveJobRequest,
    ExclusiveJobResponse,
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


@router.post(
    "/execute-exclusive/{job_name}",
    response_model=ExclusiveJobResponse,
    status_code=status.HTTP_200_OK,
    summary="Execute an exclusive job protected by Redis Distributed Lock",
)
async def execute_exclusive_job_endpoint(
    job_name: Annotated[str, Path(..., min_length=1, max_length=100, description="Unique job identifier to lock")],
    request: ExclusiveJobRequest,
    service: Annotated[JobService, Depends(get_job_service)],
) -> ExclusiveJobResponse:
    """Execute job with guaranteed mutual exclusion across cluster workers.

    Guarantees:
    - Atomicity: Acquires lock via Redis SET NX PX in O(1) time.
    - Mutex: Only 1 worker executes; concurrent competing requests receive HTTP 409 Conflict.
    - Safe Release: Token-checked release via atomic Lua script prevents lock hijacking.
    """
    result = await service.execute_exclusive_job(
        job_name=job_name,
        payload=request.payload,
        ttl_ms=request.ttl_ms,
    )
    return ExclusiveJobResponse.model_validate(result)


__all__ = [
    "router",
]
