"""Pydantic schemas for Priority-Based Background Job Scheduling."""

from datetime import datetime
from typing import Any, Optional
from pydantic import BaseModel, ConfigDict, Field

from app.core.dsa.priority_queue import JobPriority


class JobScheduleRequest(BaseModel):
    """Input payload schema for enqueuing a priority-based background job."""

    task_type: str = Field(
        ...,
        min_length=1,
        max_length=100,
        description="Type/category of background task (e.g. 'welcome_email', 'audit_log')",
    )
    payload: dict[str, Any] = Field(
        default_factory=dict,
        description="Arbitrary parameters passed to worker executor",
    )
    priority: JobPriority = Field(
        default=JobPriority.NORMAL,
        description="Job urgency level: 1=CRITICAL, 2=HIGH, 3=NORMAL, 4=LOW",
    )
    delay_seconds: float = Field(
        default=0.0,
        ge=0.0,
        le=86400.0,
        description="Execution delay in seconds (0 = immediate execution)",
    )


class JobScheduleResponse(BaseModel):
    """Output response schema returned upon successful job enqueuing."""

    job_id: str = Field(..., description="Unique generated job identifier")
    task_type: str = Field(..., description="Scheduled task identifier")
    priority: str = Field(..., description="Priority label (CRITICAL, HIGH, NORMAL, LOW)")
    scheduled_at: datetime = Field(..., description="Timestamp when job becomes eligible for execution")
    status: str = Field(default="scheduled", description="Initial scheduling status")

    model_config = ConfigDict(from_attributes=True)


class JobStatusResponse(BaseModel):
    """Output projection for Priority Queue scheduler telemetry."""

    queue_size: int = Field(..., description="Current count of queued jobs in min-heap")
    has_pending_jobs: bool = Field(..., description="True if queue length > 0")
    next_job_priority: Optional[str] = Field(
        default=None, description="Priority label of the top-of-heap job"
    )
    next_job_scheduled_at: Optional[datetime] = Field(
        default=None, description="Scheduled time of next pending root job"
    )

    model_config = ConfigDict(from_attributes=True)
