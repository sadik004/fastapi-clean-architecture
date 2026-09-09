"""Pydantic schemas for Apache Kafka CloudEvents and publishing responses."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class OrderCreatedEvent(BaseModel):
    """Event published when a customer successfully places a new order."""

    model_config = ConfigDict(frozen=True)

    event_id: str = Field(
        default_factory=lambda: f"evt_{uuid.uuid4().hex[:12]}",
        description="Unique distributed event identifier",
    )
    event_type: str = Field(
        default="order.created",
        description="Canonical domain event type",
    )
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="Event creation UTC timestamp",
    )
    user_id: int = Field(
        ...,
        description="Identifier of the user placing the order (used for partition key)",
        gt=0,
        examples=[42],
    )
    order_id: str = Field(
        ...,
        description="Unique business order identifier",
        min_length=3,
        max_length=64,
        examples=["ord_2026_0909_001"],
    )
    total_amount: float = Field(
        ...,
        description="Total monetary value of the order",
        gt=0.0,
        examples=[199.99],
    )
    currency: str = Field(
        default="USD",
        description="ISO 4217 3-letter currency code",
        min_length=3,
        max_length=3,
        examples=["USD"],
    )


class PaymentProcessedEvent(BaseModel):
    """Event published when a transaction is settled or rejected by a payment gateway."""

    model_config = ConfigDict(frozen=True)

    event_id: str = Field(
        default_factory=lambda: f"evt_{uuid.uuid4().hex[:12]}",
        description="Unique distributed event identifier",
    )
    event_type: str = Field(
        default="payment.processed",
        description="Canonical domain event type",
    )
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="Event processing UTC timestamp",
    )
    order_id: str = Field(
        ...,
        description="Order identifier associated with the payment (used for partition key)",
        min_length=3,
        max_length=64,
        examples=["ord_2026_0909_001"],
    )
    payment_id: str = Field(
        ...,
        description="Gateway payment transaction identifier",
        min_length=3,
        max_length=64,
        examples=["pay_stripe_8876"],
    )
    status: str = Field(
        ...,
        description="Payment resolution status (e.g. 'SUCCESS', 'FAILED', 'REFUNDED')",
        examples=["SUCCESS"],
    )
    amount: float = Field(
        ...,
        description="Settled transaction amount",
        gt=0.0,
        examples=[199.99],
    )


class KafkaPublishResponse(BaseModel):
    """Metadata response confirming record commit to Apache Kafka commit log."""

    model_config = ConfigDict(frozen=True)

    status: str = Field(default="COMMITTED", description="Event publication status")
    topic: str = Field(..., description="Target Kafka topic")
    partition: int = Field(..., description="Assigned integer partition index")
    offset: int = Field(..., description="Sequential append-only log offset")
    key: str = Field(..., description="Partition routing key used for hashing")
    timestamp: int = Field(..., description="Kafka record server timestamp (Unix ms)")
    message: str = Field(..., description="Human-readable status summary")


class KafkaPollBatchRequest(BaseModel):
    """Request payload to trigger controlled batch consumption."""

    model_config = ConfigDict(frozen=True)

    topic: str = Field(
        default="orders.events",
        description="Target topic to consume from",
        examples=["orders.events"],
    )
    group_id: str = Field(
        default="order-processing-group",
        description="Logical Kafka consumer group",
        examples=["order-processing-group"],
    )
    batch_size: int = Field(
        default=10,
        description="Maximum records to process and commit in this cycle",
        gt=0,
        le=100,
        examples=[10],
    )


class KafkaPartitionLag(BaseModel):
    """Partition-level consumer lag metrics."""

    model_config = ConfigDict(frozen=True)

    partition: int = Field(..., description="Partition index")
    current_offset: int = Field(..., description="Last committed offset of the consumer group")
    end_offset: int = Field(..., description="Latest log end offset (high watermark)")
    lag: int = Field(..., description="Number of unconsumed records remaining (end_offset - current_offset)")


class KafkaConsumerGroupStatus(BaseModel):
    """Telemetry report describing health, partition assignment, and consumer lag."""

    model_config = ConfigDict(frozen=True)

    group_id: str = Field(..., description="Consumer group identifier")
    topic: str = Field(..., description="Inspected topic")
    state: str = Field(default="STABLE", description="Consumer group rebalance state")
    assigned_partitions: list[int] = Field(..., description="List of partition indices currently assigned")
    partitions: list[KafkaPartitionLag] = Field(..., description="Per-partition metrics and lag breakdown")
    total_lag: int = Field(..., description="Sum of unconsumed records across all assigned partitions")


class KafkaPollBatchResponse(BaseModel):
    """Result of controlled batch consumption and manual offset commit."""

    model_config = ConfigDict(frozen=True)

    topic: str = Field(..., description="Consumed topic")
    group_id: str = Field(..., description="Consumer group that processed the batch")
    records_processed: int = Field(..., description="Count of successfully processed records")
    committed_offsets: dict[int, int] = Field(
        ...,
        description="Map of partition index to newly committed offset",
    )
    events: list[dict[str, Any]] = Field(
        default_factory=list,
        description="List of deserialized event payloads",
    )
    message: str = Field(default="Batch processed and offsets committed successfully")
