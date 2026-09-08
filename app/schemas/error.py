"""Standardized Error Response Models and Envelopes.

This module defines the unified enterprise error schema contract for the entire API surface.
Every error response (4xx, 5xx) maps to this predictable envelope.
"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ErrorDetail(BaseModel):
    """Structured error payload providing machine-readable code, message, and tracing."""

    code: str = Field(
        ...,
        description="Standardized uppercase machine-readable error code (e.g. ENTITY_NOT_FOUND)",
    )
    message: str = Field(
        ...,
        description="Human-readable description of the error",
    )
    status_code: int = Field(
        ...,
        description="HTTP status code corresponding to the error",
    )
    timestamp: datetime = Field(
        ...,
        description="UTC timestamp when the error was generated",
    )
    trace_id: str = Field(
        ...,
        description="Unique UUID correlation trace identifier for diagnostic tracking",
    )
    details: list[dict[str, Any]] | None = Field(
        default=None,
        description="Optional list of contextual field-level validation issues or metadata",
    )

    model_config = ConfigDict(frozen=True)


class ErrorResponse(BaseModel):
    """Centralized enterprise error envelope containing structured error details."""

    error: ErrorDetail = Field(
        ...,
        description="Primary error detail object",
    )
    detail: Any | None = Field(
        default=None,
        description="Backward-compatible detail payload (string message or validation error list) for legacy clients",
    )

    model_config = ConfigDict(frozen=True)
