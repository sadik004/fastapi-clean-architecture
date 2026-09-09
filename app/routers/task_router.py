"""Task management router exposing asynchronous job dispatch and polling endpoints."""

from __future__ import annotations

from fastapi import APIRouter, status

from app.schemas.task import (
    ReportGenerateRequest,
    TaskDispatchResponse,
    TaskStatusResponse,
)
from app.services.task_service import TaskService

router = APIRouter(prefix="/tasks", tags=["Tasks"])


@router.post(
    "/reports/generate",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=TaskDispatchResponse,
    summary="Dispatch asynchronous PDF report generation",
)
def generate_report(request: ReportGenerateRequest) -> TaskDispatchResponse:
    """Queue a heavy PDF report generation job into the distributed Celery queue.

    Returns HTTP 202 Accepted immediately with a unique task_id for non-blocking processing.
    """
    task_id = TaskService.dispatch_report_generation(
        user_id=request.user_id,
        report_type=request.report_type,
    )
    return TaskDispatchResponse(
        task_id=task_id,
        status="PENDING",
        message="Report generation task dispatched successfully.",
    )


@router.get(
    "/reports/status/{task_id}",
    status_code=status.HTTP_200_OK,
    response_model=TaskStatusResponse,
    summary="Poll task execution status and result",
)
def get_report_status(task_id: str) -> TaskStatusResponse:
    """Check task status and retrieve resulting artifact metadata upon completion."""
    status_data = TaskService.get_task_status(task_id=task_id)
    return TaskStatusResponse(
        task_id=status_data["task_id"],
        status=status_data["status"],
        result=status_data.get("result"),
        error=status_data.get("error"),
    )
