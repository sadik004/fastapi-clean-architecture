"""Pydantic schemas for Transactional Outbox endpoints and telemetry."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class OutboxEventResponse(BaseModel):
    """Schema representing an outbox event record."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID = Field(description="Monotonic UUIDv7 event identifier")
    event_type: str = Field(description="Domain event classification")
    aggregate_type: str = Field(description="Aggregate classification")
    aggregate_id: str = Field(description="Partition key / entity identifier")
    payload: dict[str, Any] = Field(description="Serialized event payload")
    status: str = Field(description="Event lifecycle state: PENDING, PUBLISHED, FAILED")
    retry_count: int = Field(description="Number of failed dispatch attempts")
    created_at: datetime = Field(description="UTC event creation timestamp")
    published_at: datetime | None = Field(default=None, description="UTC publication timestamp")


class OutboxPollResponse(BaseModel):
    """Schema representing the result of an Outbox Relay poll and dispatch cycle."""

    status: str = Field(description="Batch cycle result status (COMPLETED, IDLE, ERROR)")
    total_polled: int = Field(description="Total count of pending records evaluated")
    published_count: int = Field(description="Count of records successfully dispatched to broker")
    failed_count: int = Field(description="Count of records encountering broker exceptions")
    message: str = Field(description="Human-readable execution summary")


class OrderWithOutboxCreate(BaseModel):
    """Request schema for creating an order with atomic outbox event persistence."""

    user_id: int = Field(gt=0, description="The customer user identifier")
    total_amount: float = Field(gt=0.0, description="Order total in USD")
    status: str = Field(default="pending", description="Initial order state")
