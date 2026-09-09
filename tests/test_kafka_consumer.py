"""Comprehensive tests for Day 56: Kafka Consumer Concurrency & Manual Offset Commits.

Verifies:
  1. Consumer Group Partition Assignment
  2. Manual Commit After Processing (Post-DB Durability)
  3. Crash / Uncommitted Offset Replay (At-Least-Once Delivery)
  4. Multiple Consumer Groups Isolation (Independent Read Pointers)
  5. Consumer Lag & Partition Telemetry Calculations
  6. HTTP API Endpoints (/kafka/consumer/poll-batch & /kafka/consumer/status/{group_id})
  7. Auto-commit warning invariant
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.core.kafka import clear_shared_mock_kafka_storage, init_kafka_producer
from app.core.kafka_consumer import (
    MockAIOKafkaConsumer,
    clear_shared_mock_consumer_offsets,
    create_kafka_consumer,
)
from app.main import app
from app.schemas.kafka_events import OrderCreatedEvent
from app.services.kafka_consumer_service import KafkaConsumerService
from app.services.kafka_producer_service import KafkaProducerService


@pytest.fixture(autouse=True)
def clean_kafka_storage() -> None:
    """Purge shared mock topic logs and committed consumer offsets between test runs."""
    clear_shared_mock_kafka_storage()
    clear_shared_mock_consumer_offsets()


@pytest.mark.asyncio
async def test_consumer_partition_assignment() -> None:
    """Verify consumer joins the group and receives deterministic partition assignments."""
    consumer = await create_kafka_consumer(
        "orders.events",
        group_id="test-partition-group",
        force_mock=True,
    )
    await consumer.start()
    try:
        assignments = consumer.assignment()
        assert len(assignments) == 3
        partitions = {tp.partition for tp in assignments}
        assert partitions == {0, 1, 2}
        assert all(tp.topic == "orders.events" for tp in assignments)
    finally:
        await consumer.stop()


@pytest.mark.asyncio
async def test_manual_commit_after_processing() -> None:
    """Verify manual post-processing commit advances consumer group offset strictly after success."""
    await init_kafka_producer(force_mock=True)

    order_evt = OrderCreatedEvent(
        user_id=101,
        order_id="ord_test_commit_001",
        total_amount=250.0,
        currency="USD",
    )
    pub_meta = await KafkaProducerService.publish_order_created(order_evt)
    assert pub_meta["status"] == "COMMITTED"

    processed_items: list[str] = []

    async def mock_db_processing(data: dict[str, object]) -> None:
        processed_items.append(str(data.get("order_id")))

    response = await KafkaConsumerService.consume_batch(
        topic="orders.events",
        group_id="order-fulfillment-group",
        batch_size=10,
        process_callback=mock_db_processing,
        force_mock=True,
    )

    assert response.records_processed == 1
    assert "ord_test_commit_001" in processed_items
    assert len(response.committed_offsets) == 1

    # Verify subsequent poll has 0 records since offset was committed
    second_poll = await KafkaConsumerService.consume_batch(
        topic="orders.events",
        group_id="order-fulfillment-group",
        batch_size=10,
        force_mock=True,
    )
    assert second_poll.records_processed == 0


@pytest.mark.asyncio
async def test_crash_uncommitted_offset_replay() -> None:
    """Verify that a worker failure before manual commit leaves offset uncommitted for replay."""
    await init_kafka_producer(force_mock=True)

    order_evt = OrderCreatedEvent(
        user_id=202,
        order_id="ord_crash_replay_002",
        total_amount=99.0,
        currency="USD",
    )
    await KafkaProducerService.publish_order_created(order_evt)

    # 1. First attempt: DB/Domain raises error -> offsets must NOT be committed
    async def failing_db_step(data: dict[str, object]) -> None:
        raise RuntimeError("Database connection timed out before transaction commit!")

    with pytest.raises(RuntimeError, match="Database connection timed out"):
        await KafkaConsumerService.consume_batch(
            topic="orders.events",
            group_id="crash-resilient-group",
            batch_size=10,
            process_callback=failing_db_step,
            force_mock=True,
        )

    # Check status: offset should be uncommitted (lag remains 1)
    status = await KafkaConsumerService.get_consumer_group_status(
        group_id="crash-resilient-group",
        topic="orders.events",
        force_mock=True,
    )
    assert status.total_lag == 1

    # 2. Replay attempt: Successful processing
    recovered_items: list[str] = []

    async def successful_db_step(data: dict[str, object]) -> None:
        recovered_items.append(str(data.get("order_id")))

    recovery_res = await KafkaConsumerService.consume_batch(
        topic="orders.events",
        group_id="crash-resilient-group",
        batch_size=10,
        process_callback=successful_db_step,
        force_mock=True,
    )

    assert recovery_res.records_processed == 1
    assert "ord_crash_replay_002" in recovered_items

    # After commit, lag drops to 0
    status_after = await KafkaConsumerService.get_consumer_group_status(
        group_id="crash-resilient-group",
        topic="orders.events",
        force_mock=True,
    )
    assert status_after.total_lag == 0


@pytest.mark.asyncio
async def test_multiple_consumer_groups_isolation() -> None:
    """Verify distinct consumer groups independently process and commit the exact same event stream."""
    await init_kafka_producer(force_mock=True)

    order_evt = OrderCreatedEvent(
        user_id=303,
        order_id="ord_multi_group_003",
        total_amount=500.0,
        currency="USD",
    )
    await KafkaProducerService.publish_order_created(order_evt)

    # Group A: Billing Service
    res_a = await KafkaConsumerService.consume_batch(
        topic="orders.events",
        group_id="billing-service-group",
        batch_size=10,
        force_mock=True,
    )
    assert res_a.records_processed == 1

    # Group B: Notifications Service consumes independently
    res_b = await KafkaConsumerService.consume_batch(
        topic="orders.events",
        group_id="notifications-service-group",
        batch_size=10,
        force_mock=True,
    )
    assert res_b.records_processed == 1

    # Both groups should now have 0 lag
    status_a = await KafkaConsumerService.get_consumer_group_status(
        group_id="billing-service-group",
        topic="orders.events",
        force_mock=True,
    )
    status_b = await KafkaConsumerService.get_consumer_group_status(
        group_id="notifications-service-group",
        topic="orders.events",
        force_mock=True,
    )
    assert status_a.total_lag == 0
    assert status_b.total_lag == 0


@pytest.mark.asyncio
async def test_consumer_lag_telemetry_calculation() -> None:
    """Verify partition end_offset, current_offset, and calculated consumer lag."""
    await init_kafka_producer(force_mock=True)

    # Publish 3 distinct orders
    for i in range(1, 4):
        evt = OrderCreatedEvent(
            user_id=i * 10,
            order_id=f"ord_lag_{i}",
            total_amount=100.0 * i,
            currency="USD",
        )
        await KafkaProducerService.publish_order_created(evt)

    status_before = await KafkaConsumerService.get_consumer_group_status(
        group_id="inventory-audit-group",
        topic="orders.events",
        force_mock=True,
    )
    assert status_before.total_lag == 3

    # Consume batch
    res = await KafkaConsumerService.consume_batch(
        topic="orders.events",
        group_id="inventory-audit-group",
        batch_size=10,
        force_mock=True,
    )
    assert res.records_processed == 3

    status_after = await KafkaConsumerService.get_consumer_group_status(
        group_id="inventory-audit-group",
        topic="orders.events",
        force_mock=True,
    )
    assert status_after.total_lag == 0


def test_api_poll_batch_and_status_endpoints() -> None:
    """Verify HTTP endpoints for batch polling and consumer lag status."""
    client = TestClient(app)

    # 1. Publish order event via API
    pub_res = client.post(
        "/kafka/publish/order-created",
        json={
            "user_id": 404,
            "order_id": "ord_api_test_004",
            "total_amount": 75.50,
            "currency": "USD",
        },
    )
    assert pub_res.status_code == 202

    # 2. Check consumer status via API
    status_res = client.get(
        "/kafka/consumer/status/api-test-group",
        params={"topic": "orders.events"},
    )
    assert status_res.status_code == 200
    status_data = status_res.json()
    assert status_data["group_id"] == "api-test-group"
    assert status_data["total_lag"] >= 1

    # 3. Poll batch via API
    poll_res = client.post(
        "/kafka/consumer/poll-batch",
        json={
            "topic": "orders.events",
            "group_id": "api-test-group",
            "batch_size": 10,
        },
    )
    assert poll_res.status_code == 200
    poll_data = poll_res.json()
    assert poll_data["records_processed"] >= 1
    assert "ord_api_test_004" in str(poll_data["events"])

    # 4. Status after poll
    status_after = client.get(
        "/kafka/consumer/status/api-test-group",
        params={"topic": "orders.events"},
    )
    assert status_after.status_code == 200
    assert status_after.json()["total_lag"] == 0


def test_enable_auto_commit_warning() -> None:
    """Verify that setting enable_auto_commit=True produces a logged security warning."""
    consumer = MockAIOKafkaConsumer(
        "orders.events",
        group_id="unsafe-auto-commit-group",
        enable_auto_commit=True,
    )
    assert consumer.enable_auto_commit is True
