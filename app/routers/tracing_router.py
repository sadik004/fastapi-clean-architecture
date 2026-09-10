"""Diagnostics and telemetry API endpoints for OpenTelemetry Distributed Tracing."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from app.core.tracing import (
    clear_in_memory_spans,
    format_span_id,
    format_trace_id,
    get_in_memory_exporter,
)
from app.services.traced_order_service import TracedOrderService

router = APIRouter(prefix="/observability/tracing", tags=["Observability - Distributed Tracing"])
order_service = TracedOrderService()


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


@router.post(
    "/order-flow",
    status_code=status.HTTP_200_OK,
    summary="Execute multi-span order workflow",
    description="Dispatches a traced multi-step checkout workflow with parent and child spans.",
)
async def trigger_order_flow(payload: OrderFlowRequest) -> dict[str, Any]:
    """Execute multi-span checkout workflow and return generated trace metadata."""
    try:
        result = await order_service.execute_checkout_flow(
            order_id=payload.order_id,
            user_id=payload.user_id,
            item_id=payload.item_id,
            quantity=payload.quantity,
            amount=payload.amount,
            fail_at_step=payload.fail_at_step,
        )
        return result
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Checkout workflow failed: {exc}",
        ) from exc


@router.get(
    "/spans/{trace_id}",
    response_model=TraceSpansResponse,
    status_code=status.HTTP_200_OK,
    summary="Retrieve recorded spans for a Trace ID",
    description="Fetches all in-memory OpenTelemetry spans matching the specified 32-character hexadecimal trace ID.",
)
async def get_spans_by_trace_id(trace_id: str) -> TraceSpansResponse:
    """Query in-memory span exporter for all spans belonging to the given trace_id."""
    clean_trace_id = trace_id.strip().lower()
    exporter = get_in_memory_exporter()
    recorded_spans = exporter.get_finished_spans()

    matching_spans: list[SpanDetail] = []
    for s in recorded_spans:
        s_trace_id = format_trace_id(s.context.trace_id)
        if s_trace_id == clean_trace_id:
            parent_id = format_span_id(s.parent.span_id) if s.parent and s.parent.span_id else None
            # Calculate duration from nanoseconds
            duration_ms = round(((s.end_time or 0) - (s.start_time or 0)) / 1_000_000.0, 3)
            matching_spans.append(
                SpanDetail(
                    name=s.name,
                    span_id=format_span_id(s.context.span_id),
                    parent_span_id=parent_id,
                    trace_id=s_trace_id,
                    status=s.status.status_code.name,
                    duration_ms=duration_ms,
                    attributes=dict(s.attributes or {}),
                )
            )

    return TraceSpansResponse(
        trace_id=clean_trace_id,
        total_spans=len(matching_spans),
        spans=matching_spans,
    )


@router.delete(
    "/spans",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Purge in-memory spans",
    description="Resets the in-memory span exporter buffer.",
)
async def purge_spans() -> None:
    """Clear in-memory span buffer."""
    clear_in_memory_spans()
