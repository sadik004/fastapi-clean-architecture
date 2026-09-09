"""Pydantic Data Transfer Objects (DTOs) for Orders with UUIDv7 Identifiers."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class OrderCreate(BaseModel):
    """Request DTO for placing an order."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    user_id: int = Field(..., ge=1, description="ID of the ordering user")
    total_amount: float = Field(..., gt=0, description="Total order amount in currency units")


class OrderResponse(BaseModel):
    """Response DTO representing a persisted order with its UUIDv7 identifier."""

    model_config = ConfigDict(from_attributes=True, frozen=True)

    id: uuid.UUID = Field(..., description="RFC 9562 UUIDv7 Primary Key")
    user_id: int = Field(..., description="ID of the ordering customer")
    total_amount: float = Field(..., description="Total purchase value")
    status: str = Field(..., description="Current order state")
    created_at: datetime = Field(..., description="Database row creation timestamp")
    extracted_timestamp: datetime | None = Field(
        default=None,
        description="O(1) derived UTC timestamp directly extracted from UUIDv7 bits",
    )


class OrderExtractedTimestampResponse(BaseModel):
    """Diagnostic response demonstrating O(1) creation time extraction without database queries."""

    model_config = ConfigDict(frozen=True)

    order_id: uuid.UUID = Field(..., description="The queried UUIDv7 identifier")
    extracted_timestamp_utc: datetime = Field(..., description="Extracted UTC datetime from 48-bit epoch timestamp")
    time_difference_ms: float = Field(..., description="Delta in milliseconds between extracted timestamp and DB created_at")
