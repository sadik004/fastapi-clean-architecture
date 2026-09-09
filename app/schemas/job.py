"""Pydantic schemas for Priority-Based Background Job Scheduling."""

from datetime import datetime
from typing import Any

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
    next_job_priority: str | None = Field(default=None, description="Priority label of the top-of-heap job")
    next_job_scheduled_at: datetime | None = Field(default=None, description="Scheduled time of next pending root job")

    model_config = ConfigDict(from_attributes=True)


class ExclusiveJobRequest(BaseModel):
    """Input payload schema for requesting exclusive distributed job execution."""

    payload: dict[str, Any] = Field(
        default_factory=dict,
        description="Arbitrary parameters passed to worker executor",
    )
    ttl_ms: int = Field(
        default=5000,
        ge=100,
        le=60000,
        description="Distributed lock TTL in milliseconds",
    )


class ExclusiveJobResponse(BaseModel):
    """Output response schema returned upon successful exclusive job execution."""

    job_name: str = Field(..., description="Name of the exclusively executed job")
    status: str = Field(default="completed", description="Execution outcome")
    execution_token: str = Field(..., description="Unique distributed lock token that executed this job")
    payload: dict[str, Any] = Field(default_factory=dict, description="Payload processed by the job")
    executed_at: datetime = Field(..., description="Timestamp when job execution completed")

    model_config = ConfigDict(from_attributes=True)
