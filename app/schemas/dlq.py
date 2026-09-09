"""Pydantic schemas for Dead Letter Queue (DLQ) forensic envelopes and operations."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class DLQEnvelope(BaseModel):
    """Forensic envelope encapsulating a poisoned or exhausted dead-letter message."""

    model_config = ConfigDict(frozen=True)

    message_id: str = Field(
        default_factory=lambda: f"msg_{uuid.uuid4().hex[:12]}",
        description="Unique message correlation identifier",
    )
    original_topic_or_queue: str = Field(
        ...,
        description="Source queue or topic where the failure originated",
        examples=["orders.events"],
    )
    payload: dict[str, Any] = Field(
        ...,
        description="Original unmutated message payload",
    )
    error_message: str = Field(
        ...,
        description="Primary exception message causing the failure",
        examples=["KeyError: 'customer_id'"],
    )
    error_traceback: str = Field(
        ...,
        description="Captured execution stack trace for forensic analysis",
    )
    retry_count: int = Field(
        ...,
        description="Number of failed execution attempts prior to quarantine",
        ge=0,
        examples=[3],
    )
    failed_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="UTC timestamp when the message was quarantined into DLQ",
    )
    dlq_destination: str = Field(
        default="orders.dlq",
        description="Quarantine destination where the dead letter is routed",
    )


class DLQRedriveRequest(BaseModel):
    """Payload to trigger operational re-injection of quarantined messages."""

    model_config = ConfigDict(frozen=True)

    queue_or_topic: str | None = Field(
        default=None,
        description="Specific destination to redrive (None redrives all quarantined messages)",
    )
    limit: int = Field(
        default=100,
        description="Maximum number of dead-letter messages to re-inject in this run",
        gt=0,
        le=1000,
        examples=[50],
    )


class DLQRedriveResponse(BaseModel):
    """Summary of redriven messages re-injected into the primary pipeline."""

    model_config = ConfigDict(frozen=True)

    redriven_count: int = Field(..., description="Number of quarantined messages re-dispatched")
    target_destination: str = Field(..., description="Destination queue or topic")
    message: str = Field(..., description="Status summary")


class DLQPurgeResponse(BaseModel):
    """Summary of purged DLQ messages."""

    model_config = ConfigDict(frozen=True)

    purged_count: int = Field(..., description="Number of quarantined messages permanently removed")
    message: str = Field(..., description="Status summary")


class DLQMessageListResponse(BaseModel):
    """Response containing quarantined dead-letter messages for forensic inspection."""

    model_config = ConfigDict(frozen=True)

    total_quarantined: int = Field(..., description="Total count of active quarantined messages")
    messages: list[DLQEnvelope] = Field(..., description="List of forensic envelopes")
