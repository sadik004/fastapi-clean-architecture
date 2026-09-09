"""Transactional Outbox Router exposing relay polling, event listing, and atomic creation."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, status

from app.core.dependencies import get_order_service, get_outbox_relay_service, get_uow
from app.core.unit_of_work import UnitOfWorkProtocol
from app.schemas.order import OrderResponse
from app.schemas.outbox import (
    OrderWithOutboxCreate,
    OutboxEventResponse,
    OutboxPollResponse,
)
from app.services.order_service import OrderService
from app.services.outbox_relay_service import OutboxRelayService

router = APIRouter(prefix="/outbox", tags=["Transactional Outbox & Dual-Write Mitigation"])


@router.post(
    "/relay/poll",
    response_model=OutboxPollResponse,
    status_code=status.HTTP_200_OK,
    summary="Trigger Outbox Relay poll and dispatch batch",
)
async def poll_outbox_relay_endpoint(
    relay_service: Annotated[OutboxRelayService, Depends(get_outbox_relay_service)],
    batch_size: Annotated[int, Query(ge=1, le=500, description="Maximum batch size to dispatch")] = 50,
) -> OutboxPollResponse:
    """Execute a single Outbox Relay cycle to publish pending events to Apache Kafka."""
    result = await relay_service.poll_and_publish_pending_events(batch_size=batch_size)
    return OutboxPollResponse(
        status=result["status"],
        total_polled=result["total_polled"],
        published_count=result["published_count"],
        failed_count=result["failed_count"],
        message=result["message"],
    )


@router.get(
    "/events",
    response_model=list[OutboxEventResponse],
    status_code=status.HTTP_200_OK,
    summary="List recent outbox events for observability",
)
async def list_outbox_events_endpoint(
    uow: Annotated[UnitOfWorkProtocol, Depends(get_uow)],
    limit: Annotated[int, Query(ge=1, le=100, description="Maximum event records to retrieve")] = 50,
) -> list[OutboxEventResponse]:
    """Inspect recent outbox records across PENDING, PUBLISHED, and FAILED states."""
    async with uow:
        records = await uow.outbox.list_recent_events(limit=limit)
        return [
            OutboxEventResponse(
                id=r.id,
                event_type=r.event_type,
                aggregate_type=r.aggregate_type,
                aggregate_id=r.aggregate_id,
                payload=r.payload,
                status=r.status,
                retry_count=r.retry_count,
                created_at=r.created_at,
                published_at=r.published_at,
            )
            for r in records
        ]


@router.post(
    "/orders",
    response_model=dict[str, Any],
    status_code=status.HTTP_201_CREATED,
    summary="Create order with atomic outbox event persistence",
)
async def create_order_with_outbox_endpoint(
    payload: OrderWithOutboxCreate,
    order_service: Annotated[OrderService, Depends(get_order_service)],
) -> dict[str, Any]:
    """Create an order and record an outbound event in the same local ACID transaction."""
    order, outbox_event = await order_service.create_order_with_outbox(
        user_id=payload.user_id,
        total_amount=payload.total_amount,
        status=payload.status,
    )
    extracted_ts = order_service.extract_order_timestamp(order.id)

    order_resp = OrderResponse(
        id=order.id,
        user_id=order.user_id,
        total_amount=order.total_amount,
        status=order.status,
        created_at=order.created_at,
        extracted_timestamp=extracted_ts,
    )
    outbox_resp = OutboxEventResponse(
        id=outbox_event.id,
        event_type=outbox_event.event_type,
        aggregate_type=outbox_event.aggregate_type,
        aggregate_id=outbox_event.aggregate_id,
        payload=outbox_event.payload,
        status=outbox_event.status,
        retry_count=outbox_event.retry_count,
        created_at=outbox_event.created_at,
        published_at=outbox_event.published_at,
    )

    return {
        "order": order_resp.model_dump(mode="json"),
        "outbox_event": outbox_resp.model_dump(mode="json"),
        "message": "Order and Outbox event committed atomically in a single ACID transaction.",
    }
