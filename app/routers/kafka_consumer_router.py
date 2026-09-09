"""FastAPI Router exposing Apache Kafka Consumer Concurrency & Lag Telemetry."""

from __future__ import annotations

from fastapi import APIRouter, Query, status

from app.schemas.kafka_events import (
    KafkaConsumerGroupStatus,
    KafkaPollBatchRequest,
    KafkaPollBatchResponse,
)
from app.services.kafka_consumer_service import KafkaConsumerService

router = APIRouter(prefix="/kafka/consumer", tags=["Kafka Consumer Groups & Concurrency"])


@router.get(
    "/status/{group_id}",
    response_model=KafkaConsumerGroupStatus,
    summary="Get Kafka consumer group status, partition assignments, and lag telemetry",
)
async def get_consumer_status(
    group_id: str,
    topic: str = Query(
        default="orders.events",
        description="Kafka topic to inspect",
    ),
) -> KafkaConsumerGroupStatus:
    """Retrieve real-time consumer group metrics including assigned partitions, committed offsets, and lag."""
    return await KafkaConsumerService.get_consumer_group_status(
        group_id=group_id,
        topic=topic,
    )


@router.post(
    "/poll-batch",
    status_code=status.HTTP_200_OK,
    response_model=KafkaPollBatchResponse,
    summary="Trigger a controlled consumer batch poll with manual offset commit",
)
async def poll_batch(request: KafkaPollBatchRequest) -> KafkaPollBatchResponse:
    """Execute a single bounded batch poll cycle with manual post-processing offset commit."""
    return await KafkaConsumerService.consume_batch(
        topic=request.topic,
        group_id=request.group_id,
        batch_size=request.batch_size,
    )
