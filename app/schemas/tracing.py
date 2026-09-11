"""Pydantic DTO Schemas for OpenTelemetry Distributed Tracing Diagnostics."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class OrderFlowRequest(BaseModel):
    """Payload for triggering a multi-span order checkout workflow."""

    order_id: str = Field(default="ord_demo_12345", description="Unique order identifier")
    user_id: str = Field(default="usr_test_99", description="Customer user ID")
    item_id: str = Field(default="prod_laptop_01", description="Item product code")
    quantity: int = Field(default=1, ge=1, description="Quantity")
    amount: float = Field(default=1299.99, gt=0, description="Total monetary charge")
    fail_at_step: str | None = Field(default=None, description="Optional step to fail ('inventory', 'payment', 'kafka')")


class SpanDetail(BaseModel):
    """Telemetry detail of an individual recorded OpenTelemetry span."""

    name: str
    span_id: str
    parent_span_id: str | None
    trace_id: str
    status: str
    duration_ms: float
    attributes: dict[str, Any]


class TraceSpansResponse(BaseModel):
    """Response envelope containing all recorded spans for a trace ID."""

    trace_id: str
    total_spans: int
    spans: list[SpanDetail]
