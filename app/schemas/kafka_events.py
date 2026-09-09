"""Pydantic schemas for Apache Kafka CloudEvents and publishing responses."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

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
