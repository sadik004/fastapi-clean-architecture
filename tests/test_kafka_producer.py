"""Comprehensive Verification Suite for Enterprise Apache Kafka Event Streaming Producer.

Validates:
1. Deterministic key-based partitioning (same entity key always targets exact same partition).
2. CloudEvent schema serialization (OrderCreatedEvent and PaymentProcessedEvent).
3. Monotonically increasing sequential append-only offsets per partition.
4. Production producer configuration hardening (acks='all', enable_idempotence=True, compression_type='gzip').
5. HTTP REST endpoints (/kafka/publish/order-created, /kafka/publish/payment-processed) returning HTTP 202.
"""

from __future__ import annotations

from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError

from app.core.kafka import MockAIOKafkaProducer, init_kafka_producer
from app.main import app
from app.schemas.kafka_events import OrderCreatedEvent, PaymentProcessedEvent
from app.services.kafka_producer_service import (
    TOPIC_ORDERS,
    TOPIC_PAYMENTS,
    KafkaProducerService,
)


@pytest_asyncio.fixture(autouse=True)
async def setup_kafka_test_producer() -> Any:
    """Initialize in-memory MockAIOKafkaProducer and clear logs before each test."""
    producer = await init_kafka_producer(force_mock=True)
    if isinstance(producer, MockAIOKafkaProducer):
        producer.clear()
    yield
    if isinstance(producer, MockAIOKafkaProducer):
        producer.clear()


@pytest.mark.asyncio
async def test_deterministic_key_based_partitioning() -> None:
    """Verify events with the identical key deterministically target the exact same partition."""
    # Publish 5 orders for user 101 and 5 orders for user 202
    user_101_partitions: set[int] = set()
    user_202_partitions: set[int] = set()

    for i in range(5):
        event_101 = OrderCreatedEvent(
            user_id=101,
            order_id=f"ord_user101_{i}",
            total_amount=50.0 + i,
        )
        res_101 = await KafkaProducerService.publish_order_created(event_101)
        user_101_partitions.add(res_101["partition"])

        event_202 = OrderCreatedEvent(
            user_id=202,
            order_id=f"ord_user202_{i}",
            total_amount=100.0 + i,
        )
        res_202 = await KafkaProducerService.publish_order_created(event_202)
        user_202_partitions.add(res_202["partition"])

    # Invariant: All events for user 101 must be in exactly ONE partition
    assert len(user_101_partitions) == 1, "User 101 events were scattered across multiple partitions!"
    # Invariant: All events for user 202 must be in exactly ONE partition
    assert len(user_202_partitions) == 1, "User 202 events were scattered across multiple partitions!"


@pytest.mark.asyncio
async def test_sequential_append_only_monotonic_offsets() -> None:
    """Verify records appended to the same topic partition receive strictly increasing offsets."""
    user_id = 777
    observed_offsets: list[int] = []

    for i in range(4):
        event = OrderCreatedEvent(
            user_id=user_id,
            order_id=f"ord_seq_{i}",
            total_amount=10.0 * (i + 1),
        )
        res = await KafkaProducerService.publish_order_created(event)
        observed_offsets.append(res["offset"])

    # Must be strictly monotonic: 0, 1, 2, 3
    assert observed_offsets == [0, 1, 2, 3]


@pytest.mark.asyncio
async def test_cloudevent_schema_validation_and_serialization() -> None:
    """Verify OrderCreatedEvent and PaymentProcessedEvent enforce constraints and serialize cleanly."""
    event_order = OrderCreatedEvent(
        user_id=42,
        order_id="ord_test_001",
        total_amount=250.75,
        currency="USD",
    )
    assert event_order.event_type == "order.created"
    assert event_order.event_id.startswith("evt_")
    assert event_order.currency == "USD"

    event_payment = PaymentProcessedEvent(
        order_id="ord_test_001",
        payment_id="pay_9999",
        status="SUCCESS",
        amount=250.75,
    )
    assert event_payment.event_type == "payment.processed"
    assert event_payment.status == "SUCCESS"

    # Test negative amounts fail validation
    with pytest.raises(ValidationError):
        OrderCreatedEvent(user_id=42, order_id="ord_bad", total_amount=-50.0)


@pytest.mark.asyncio
async def test_producer_configuration_hardening() -> None:
    """Verify producer configuration parameters match enterprise resilience standards."""
    producer = await init_kafka_producer(force_mock=True)
    assert producer.acks == "all"
    assert producer.enable_idempotence is True
    assert producer.compression_type == "gzip"
    assert producer.max_batch_size == 16384
    assert producer.linger_ms == 10


@pytest.mark.asyncio
async def test_null_or_empty_partition_key_rejection() -> None:
    """Verify publish_event rejects empty or whitespace partition keys to prevent disordered messages."""
    event = OrderCreatedEvent(user_id=1, order_id="ord_empty", total_amount=10.0)
    with pytest.raises(ValueError, match="Kafka partition key must be a non-empty string"):
        await KafkaProducerService.publish_event(
            topic=TOPIC_ORDERS,
            key="   ",
            event=event,
        )


@pytest.mark.asyncio
async def test_api_publish_order_created_endpoint() -> None:
    """Verify POST /kafka/publish/order-created endpoint returns HTTP 202 Accepted with commit metadata."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/kafka/publish/order-created",
            json={
                "user_id": 55,
                "order_id": "ord_api_live_1",
                "total_amount": 180.50,
                "currency": "USD",
            },
        )
        assert response.status_code == 202
        body = response.json()
        assert body["status"] == "COMMITTED"
        assert body["topic"] == TOPIC_ORDERS
        assert body["key"] == "user_55"
        assert isinstance(body["partition"], int)
        assert isinstance(body["offset"], int)
        assert "appended to topic" in body["message"]


@pytest.mark.asyncio
async def test_api_publish_payment_processed_endpoint() -> None:
    """Verify POST /kafka/publish/payment-processed endpoint returns HTTP 202 Accepted."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/kafka/publish/payment-processed",
            json={
                "order_id": "ord_api_live_1",
                "payment_id": "pay_stripe_555",
                "status": "SUCCESS",
                "amount": 180.50,
            },
        )
        assert response.status_code == 202
        body = response.json()
        assert body["status"] == "COMMITTED"
        assert body["topic"] == TOPIC_PAYMENTS
        assert body["key"] == "order_ord_api_live_1"
        assert isinstance(body["partition"], int)
        assert isinstance(body["offset"], int)
