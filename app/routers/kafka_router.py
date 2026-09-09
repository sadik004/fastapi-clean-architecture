"""FastAPI Router exposing Apache Kafka event streaming endpoints."""

from __future__ import annotations

from fastapi import APIRouter, status

from app.schemas.kafka_events import (
    KafkaPublishResponse,
    OrderCreatedEvent,
    PaymentProcessedEvent,
)
from app.services.kafka_producer_service import KafkaProducerService

router = APIRouter(prefix="/kafka", tags=["Event Streaming (Kafka)"])


@router.post(
    "/publish/order-created",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=KafkaPublishResponse,
    summary="Publish OrderCreatedEvent to Kafka topic",
)
async def publish_order_created(event: OrderCreatedEvent) -> KafkaPublishResponse:
    """Publish an OrderCreatedEvent with deterministic key-based partition assignment.

    Guarantees strict FIFO ordering per user across distributed partitions.
    """
    metadata = await KafkaProducerService.publish_order_created(event=event)
    return KafkaPublishResponse(**metadata)


@router.post(
    "/publish/payment-processed",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=KafkaPublishResponse,
    summary="Publish PaymentProcessedEvent to Kafka topic",
)
async def publish_payment_processed(event: PaymentProcessedEvent) -> KafkaPublishResponse:
    """Publish a PaymentProcessedEvent with order-based partition assignment."""
    metadata = await KafkaProducerService.publish_payment_processed(event=event)
    return KafkaPublishResponse(**metadata)
