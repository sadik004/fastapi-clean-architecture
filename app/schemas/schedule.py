"""Pydantic schemas for periodic schedule inspection and manual triggering."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class ScheduleEntryResponse(BaseModel):
    """Schema representing an active periodic task schedule entry."""

    model_config = ConfigDict(frozen=True)

    name: str = Field(description="Unique identifier for the scheduled task entry")
    task: str = Field(description="Fully qualified Celery task path")
    schedule_type: str = Field(description="Schedule type ('crontab' or 'interval')")
    schedule_expression: str = Field(description="Human-readable schedule expression")
    queue: str = Field(default="celery", description="Target broker queue")


class ScheduleListResponse(BaseModel):
    """Schema representing the collection of all configured schedules."""

    model_config = ConfigDict(frozen=True)

    total_schedules: int = Field(description="Total count of active registered schedules")
    schedules: list[ScheduleEntryResponse] = Field(description="List of configured schedule entries")


class ManualTriggerResponse(BaseModel):
    """Schema returned upon manually triggering an ad-hoc run of a scheduled task."""

    model_config = ConfigDict(frozen=True)

    task_name: str = Field(description="Name or key of the triggered scheduled task")
    task_id: str = Field(description="Unique Celery AsyncResult identifier")
    status: str = Field(default="PENDING", description="Initial task status")
    message: str = Field(description="User-friendly status confirmation")
