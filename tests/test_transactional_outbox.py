"""Tests for Transactional Outbox Pattern Architecture (Zero Data Loss & Dual-Write Mitigation)."""

import json
import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.unit_of_work import InMemoryUnitOfWork
from app.main import app
from app.repositories.user_repository import UserEntity
from app.services.order_service import OrderService
from app.services.outbox_relay_service import OutboxRelayService


@pytest.fixture
def in_memory_uow() -> InMemoryUnitOfWork:
    """Provide an isolated in-memory Unit of Work seeded with a test user."""
    uow = InMemoryUnitOfWork()
    user = UserEntity(
        id=1,
        email="test_outbox_user@example.com",
        username="outbox_user",
        password_hash="mock_hash",
        is_active=True,
        created_at=datetime.now(UTC),
    )
    uow.users._store[1] = user
    uow.users._email_index[user.email] = 1
    uow.users._username_index[user.username] = 1
    return uow


@pytest.mark.asyncio
async def test_atomic_colocation_order_and_outbox(in_memory_uow: InMemoryUnitOfWork) -> None:
    """Test 1: Order and Outbox event are co-located in the same ACID transaction."""
    order_service = OrderService(uow=in_memory_uow)

    order, outbox_event = await order_service.create_order_with_outbox(
        user_id=1,
        total_amount=199.99,
        status="pending",
    )

    assert order.id is not None
    assert order.total_amount == 199.99
    assert order.user_id == 1

    # Verify outbox event attributes
    assert outbox_event.id is not None
    assert outbox_event.event_type == "order.created"
    assert outbox_event.aggregate_type == "order"
    assert outbox_event.aggregate_id == str(order.id)
    assert outbox_event.status == "PENDING"
    assert outbox_event.retry_count == 0
    assert outbox_event.published_at is None
    assert outbox_event.payload["order_id"] == str(order.id)
    assert outbox_event.payload["total_amount"] == 199.99

    # Verify both exist in UoW stores
    assert in_memory_uow.orders._store[order.id].total_amount == 199.99
    assert in_memory_uow.outbox._store[outbox_event.id].status == "PENDING"


@pytest.mark.asyncio
async def test_transaction_rollback_safety(in_memory_uow: InMemoryUnitOfWork) -> None:
    """Test 2: If an error occurs midway, neither Order nor Outbox event is saved (ACID Atomicity)."""
    initial_order_count = len(in_memory_uow.orders._store)
    initial_outbox_count = len(in_memory_uow.outbox._store)

    order_id = uuid.uuid4()
    with pytest.raises(RuntimeError, match="Simulated crash before transaction commit"):
        async with in_memory_uow as uow:
            await uow.orders.create(
                user_id=1,
                total_amount=350.0,
                status="pending",
                order_id=order_id,
            )
            await uow.outbox.record_event(
                event_type="order.created",
                aggregate_type="order",
                aggregate_id=str(order_id),
                payload={"order_id": str(order_id)},
            )
            # Crash before commit
            raise RuntimeError("Simulated crash before transaction commit")

    # Assert rollback restored stores to initial state
    assert len(in_memory_uow.orders._store) == initial_order_count
    assert len(in_memory_uow.outbox._store) == initial_outbox_count
    assert order_id not in in_memory_uow.orders._store


@pytest.mark.asyncio
async def test_outbox_relay_publishes_to_kafka(in_memory_uow: InMemoryUnitOfWork) -> None:
    """Test 3: Outbox Relay polls pending events, dispatches to Kafka, and marks them PUBLISHED."""
    order_id = str(uuid.uuid4())
    async with in_memory_uow as uow:
        event = await uow.outbox.record_event(
            event_type="order.created",
            aggregate_type="order",
            aggregate_id=order_id,
            payload={"order_id": order_id, "amount": 100.0},
        )
        await uow.commit()

    assert event.status == "PENDING"

    # Mock Kafka publisher
    dispatched_messages: list[tuple[str, str, dict[str, Any]]] = []

    async def mock_publisher(topic: str, key: str, payload_bytes: bytes) -> None:
        dispatched_messages.append((topic, key, json.loads(payload_bytes.decode("utf-8"))))

    relay_service = OutboxRelayService(uow=in_memory_uow, publisher=mock_publisher)
    result = await relay_service.poll_and_publish_pending_events(batch_size=10)

    assert result["status"] == "COMPLETED"
    assert result["total_polled"] == 1
    assert result["published_count"] == 1
    assert result["failed_count"] == 0

    # Verify Kafka dispatch parameters
    assert len(dispatched_messages) == 1
    topic, key, payload = dispatched_messages[0]
    assert topic == "orders.events"
    assert key == order_id
    assert payload["amount"] == 100.0

    # Verify event status updated to PUBLISHED
    updated_event = await in_memory_uow.outbox.get_by_id(event.id)
    assert updated_event is not None
    assert updated_event.status == "PUBLISHED"
    assert updated_event.published_at is not None


@pytest.mark.asyncio
async def test_outbox_relay_kafka_downtime_resilience(in_memory_uow: InMemoryUnitOfWork) -> None:
    """Test 4: When Kafka is unreachable, outbox event safely remains PENDING with incremented retries."""
    order_id = str(uuid.uuid4())
    async with in_memory_uow as uow:
        event = await uow.outbox.record_event(
            event_type="order.created",
            aggregate_type="order",
            aggregate_id=order_id,
            payload={"order_id": order_id, "amount": 50.0},
        )
        await uow.commit()

    async def failing_publisher(topic: str, key: str, payload_bytes: bytes) -> None:
        raise ConnectionRefusedError("Kafka brokers unreachable (Simulated Broker Down)")

    relay_service = OutboxRelayService(uow=in_memory_uow, publisher=failing_publisher)
    result = await relay_service.poll_and_publish_pending_events(batch_size=10)

    assert result["status"] == "COMPLETED"
    assert result["total_polled"] == 1
    assert result["published_count"] == 0
    assert result["failed_count"] == 1

    # Invariant: Record must remain PENDING with zero data loss
    record = await in_memory_uow.outbox.get_by_id(event.id)
    assert record is not None
    assert record.status == "PENDING"
    assert record.retry_count == 1
    assert record.published_at is None


@pytest.mark.asyncio
async def test_outbox_relay_max_retries_transition(in_memory_uow: InMemoryUnitOfWork) -> None:
    """Test 5: Outbox event transitions to FAILED after reaching MAX_RETRIES (3)."""
    async with in_memory_uow as uow:
        event = await uow.outbox.record_event(
            event_type="payment.failed",
            aggregate_type="payment",
            aggregate_id="pay-123",
            payload={"reason": "Insufficient funds"},
        )
        await uow.commit()

    async def failing_publisher(topic: str, key: str, payload_bytes: bytes) -> None:
        raise TimeoutError("Kafka delivery timeout")

    relay_service = OutboxRelayService(uow=in_memory_uow, publisher=failing_publisher)

    # 3 consecutive failed cycles
    for _ in range(3):
        await relay_service.poll_and_publish_pending_events(batch_size=10)

    record = await in_memory_uow.outbox.get_by_id(event.id)
    assert record is not None
    assert record.status == "FAILED"
    assert record.retry_count >= 3


@pytest.mark.asyncio
async def test_api_outbox_relay_and_events_endpoints() -> None:
    """Test 6: HTTP endpoints for /outbox/relay/poll and /outbox/events."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # 1. Trigger poll when queue is empty
        poll_resp = await client.post("/outbox/relay/poll?batch_size=20")
        assert poll_resp.status_code == 200
        poll_data = poll_resp.json()
        assert poll_data["status"] in ("IDLE", "COMPLETED")
        assert "total_polled" in poll_data
        assert "published_count" in poll_data

        # 2. Query recent events list
        events_resp = await client.get("/outbox/events?limit=10")
        assert events_resp.status_code == 200
        events_data = events_resp.json()
        assert isinstance(events_data, list)
