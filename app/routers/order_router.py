"""Order router endpoints demonstrating UUIDv7 primary keys and O(1) timestamp extraction."""

from __future__ import annotations

import uuid
from datetime import UTC
from typing import Annotated

from fastapi import APIRouter, Depends, Path, status

from app.core.dependencies import get_order_service
from app.schemas.order import (
    OrderCreate,
    OrderExtractedTimestampResponse,
    OrderResponse,
)
from app.services.order_service import OrderService

router = APIRouter(prefix="/orders", tags=["Orders & Time-Ordered Identifiers"])


@router.post(
    "",
    response_model=OrderResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create order with RFC 9562 UUIDv7 Primary Key",
)
async def create_order_endpoint(
    payload: OrderCreate,
    service: Annotated[OrderService, Depends(get_order_service)],
) -> OrderResponse:
    """Create a new order entity with an automatically generated time-ordered UUIDv7."""
    order = await service.create_order(
        user_id=payload.user_id,
        total_amount=payload.total_amount,
    )
    # Derive creation timestamp in O(1) bit-shift time from the generated UUIDv7
    extracted_ts = service.extract_order_timestamp(order.id)

    return OrderResponse(
        id=order.id,
        user_id=order.user_id,
        total_amount=order.total_amount,
        status=order.status,
        created_at=order.created_at,
        extracted_timestamp=extracted_ts,
    )


@router.get(
    "/{order_id}",
    response_model=OrderResponse,
    status_code=status.HTTP_200_OK,
    summary="Retrieve order details by UUIDv7 identifier",
)
async def get_order_endpoint(
    order_id: Annotated[uuid.UUID, Path(description="The UUIDv7 primary key identifier of the order")],
    service: Annotated[OrderService, Depends(get_order_service)],
) -> OrderResponse:
    """Retrieve an order by its UUIDv7 identifier in O(1) B-tree lookup time."""
    order = await service.get_order_by_id(order_id=order_id)
    extracted_ts = service.extract_order_timestamp(order.id)

    return OrderResponse(
        id=order.id,
        user_id=order.user_id,
        total_amount=order.total_amount,
        status=order.status,
        created_at=order.created_at,
        extracted_timestamp=extracted_ts,
    )


@router.get(
    "/{order_id}/extracted-timestamp",
    response_model=OrderExtractedTimestampResponse,
    status_code=status.HTTP_200_OK,
    summary="Demonstrate O(1) Creation Time Extraction Directly from UUIDv7 Bits",
)
async def get_order_extracted_timestamp_endpoint(
    order_id: Annotated[uuid.UUID, Path(description="The UUIDv7 primary key identifier")],
    service: Annotated[OrderService, Depends(get_order_service)],
) -> OrderExtractedTimestampResponse:
    """Extract the creation UTC timestamp directly from the 48-bit millisecond epoch component.

    Requires zero database queries or table indexes!
    """
    order = await service.get_order_by_id(order_id=order_id)
    extracted_ts = service.extract_order_timestamp(order_id)

    # Calculate delta between database created_at and UUIDv7 48-bit timestamp
    db_created_at = order.created_at if order.created_at.tzinfo is not None else order.created_at.replace(tzinfo=UTC)
    delta_ms = abs((extracted_ts - db_created_at).total_seconds() * 1000.0)

    return OrderExtractedTimestampResponse(
        order_id=order_id,
        extracted_timestamp_utc=extracted_ts,
        time_difference_ms=round(delta_ms, 3),
    )
