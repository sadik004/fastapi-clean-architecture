"""Pydantic DTOs for ARQ asynchronous job dispatching and status inspection."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class WebhookJobRequest(BaseModel):
    """Payload schema for requesting an asynchronous outbound webhook dispatch."""

    model_config = ConfigDict(frozen=True)

    webhook_url: str = Field(description="Destination HTTP webhook endpoint")
    payload: dict[str, Any] = Field(default_factory=dict, description="JSON payload data to transmit")


class PushNotificationJobRequest(BaseModel):
    """Payload schema for requesting a high-throughput push notification broadcast."""

    model_config = ConfigDict(frozen=True)

    user_ids: list[int] = Field(min_length=1, description="Target recipient user IDs")
    message: str = Field(min_length=1, max_length=500, description="Push notification text content")


class ArqJobEnqueueResponse(BaseModel):
    """Response schema returned upon successfully enqueuing an ARQ coroutine job."""

    model_config = ConfigDict(frozen=True)

    job_id: str = Field(description="Unique ARQ job identifier")
    job_name: str = Field(description="Name of the enqueued coroutine function")
    status: str = Field(default="QUEUED", description="Initial queue dispatch status")
    message: str = Field(description="Human-readable queue confirmation")


class ArqJobStatusResponse(BaseModel):
    """Response schema returned when polling ARQ job execution status."""

    model_config = ConfigDict(frozen=True)

    job_id: str = Field(description="Unique ARQ job identifier")
    status: str = Field(description="Current job status: queued, in_progress, complete, or not_found")
    result: Any | None = Field(default=None, description="Returned result dictionary if completed")
    enqueue_time: str | None = Field(default=None, description="ISO timestamp when job was enqueued")
    success: bool | None = Field(default=None, description="Whether execution finished successfully")
