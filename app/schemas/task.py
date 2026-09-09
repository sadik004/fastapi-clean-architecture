"""Pydantic schemas for asynchronous task management."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ReportGenerateRequest(BaseModel):
    """Payload to dispatch a PDF report generation background job."""

    model_config = ConfigDict(frozen=True)

    user_id: int = Field(..., gt=0, description="Target User ID for the report")
    report_type: str = Field(
        ...,
        min_length=3,
        max_length=50,
        description="Type of report (e.g., 'financial_annual', 'audit_log', 'analytics')",
    )


class TaskDispatchResponse(BaseModel):
    """Response returned immediately upon queuing a task (HTTP 202)."""

    model_config = ConfigDict(frozen=True)

    task_id: str = Field(..., description="Unique Celery Task Identifier")
    status: str = Field(default="PENDING", description="Initial dispatch status")
    message: str = Field(..., description="Human-readable dispatch notice")


class TaskStatusResponse(BaseModel):
    """Response returned when querying task execution status."""

    model_config = ConfigDict(frozen=True)

    task_id: str = Field(..., description="Unique Celery Task Identifier")
    status: str = Field(..., description="Celery task execution state (PENDING, STARTED, SUCCESS, FAILURE, RETRY)")
    result: dict[str, Any] | None = Field(default=None, description="Result payload if task succeeded")
    error: str | None = Field(default=None, description="Error message if task failed")
