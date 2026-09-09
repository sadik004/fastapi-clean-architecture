"""Comprehensive Verification Suite for Enterprise RabbitMQ AMQP 0-9-1 Message Broker.

Validates:
1. Direct Exchange exact routing (O(1) dictionary dispatch).
2. Fanout Exchange multi-queue broadcasting.
3. Topic Exchange pattern matching with '*' and '#' wildcards.
4. Manual message acknowledgement (ACK/NACK/Requeue) invariants.
5. Persistent delivery mode enforcement (DeliveryMode.PERSISTENT).
6. HTTP API endpoints (/broker/publish/direct, /broker/publish/fanout, /broker/publish/topic, /broker/consume/{queue}).
"""

from __future__ import annotations

from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.core.rabbitmq import (
    MockAMQPQueue,
    init_rabbitmq,
)
from app.core.rabbitmq_topology import (
    DIRECT_EXCHANGE_NAME,
    FANOUT_EXCHANGE_NAME,
    QUEUE_ANALYTICS,
    QUEUE_AUDIT,
    QUEUE_CRITICAL_ALERTS,
    QUEUE_EU_LOGS,
    QUEUE_NOTIFICATION,
    QUEUE_ORDER_CANCELLED,
    QUEUE_ORDER_CREATED,
    QUEUE_ORDER_MONITORING,
    ROUTING_KEY_ORDER_CANCELLED,
    ROUTING_KEY_ORDER_CREATED,
)
from app.main import app
from app.services.message_broker_service import MessageBrokerService


@pytest_asyncio.fixture(autouse=True)
async def setup_rabbitmq_test_topology() -> Any:
    """Initialize in-memory mock AMQP channel and declare clean topology before each test."""
    await init_rabbitmq(force_mock=True)
    MessageBrokerService.reset_topology_cache()
    await MessageBrokerService.get_or_declare_topology()
    yield
    MessageBrokerService.reset_topology_cache()


@pytest.mark.asyncio
async def test_direct_exchange_routing_key_isolation() -> None:
    """Verify Direct exchange routes strictly to bound queue matching exact routing key."""
    # 1. Publish message for 'order.created'
    created_payload = {"order_id": "ord_1001", "amount": 150.0, "status": "CREATED"}
    pub_res = await MessageBrokerService.publish_direct_message(
        routing_key=ROUTING_KEY_ORDER_CREATED,
        payload=created_payload,
    )
    assert pub_res["status"] == "PUBLISHED"
    assert pub_res["exchange"] == DIRECT_EXCHANGE_NAME

    # 2. Consume from order.created queue
    consumed = await MessageBrokerService.consume_next_message(QUEUE_ORDER_CREATED)
    assert consumed is not None
    assert consumed["payload"] == created_payload
    assert consumed["routing_key"] == ROUTING_KEY_ORDER_CREATED
    assert consumed["acknowledged"] is True

    # 3. Verify order.cancelled queue received ZERO messages
    empty_res = await MessageBrokerService.consume_next_message(QUEUE_ORDER_CANCELLED)
    assert empty_res is None

    # 4. Now publish to 'order.cancelled'
    cancelled_payload = {"order_id": "ord_1002", "reason": "Customer request"}
    await MessageBrokerService.publish_direct_message(
        routing_key=ROUTING_KEY_ORDER_CANCELLED,
        payload=cancelled_payload,
    )
    consumed_cancel = await MessageBrokerService.consume_next_message(QUEUE_ORDER_CANCELLED)
    assert consumed_cancel is not None
    assert consumed_cancel["payload"] == cancelled_payload


@pytest.mark.asyncio
async def test_fanout_exchange_multi_queue_broadcasting() -> None:
    """Verify Fanout exchange broadcasts messages to ALL bound queues ignoring routing keys."""
    event_payload = {
        "event_id": "evt_broadcast_1",
        "event_name": "user.signup",
        "user_email": "alice@example.com",
    }

    # 1. Broadcast event across fanout exchange
    pub_res = await MessageBrokerService.publish_fanout_event(payload=event_payload)
    assert pub_res["status"] == "PUBLISHED"
    assert pub_res["exchange"] == FANOUT_EXCHANGE_NAME

    # 2. Assert notification, analytics, and audit queues ALL receive the message
    for queue_name in [QUEUE_NOTIFICATION, QUEUE_ANALYTICS, QUEUE_AUDIT]:
        msg = await MessageBrokerService.consume_next_message(queue_name=queue_name)
        assert msg is not None, f"Queue {queue_name} failed to receive fanout message"
        assert msg["payload"] == event_payload
        assert msg["queue_name"] == queue_name
        assert msg["acknowledged"] is True

    # 3. Verify queues are now empty
    for queue_name in [QUEUE_NOTIFICATION, QUEUE_ANALYTICS, QUEUE_AUDIT]:
        assert await MessageBrokerService.consume_next_message(queue_name=queue_name) is None


@pytest.mark.asyncio
async def test_topic_exchange_wildcard_pattern_matching() -> None:
    """Verify Topic exchange routes correctly according to * and # wildcard patterns."""
    # Pattern bindings:
    # QUEUE_EU_LOGS -> 'europe.#'
    # QUEUE_CRITICAL_ALERTS -> '#.critical'
    # QUEUE_ORDER_MONITORING -> 'order.*.*'

    # Case A: 'order.eu.critical'
    # Should match:
    # - 'order.*.*' (3 words, starts with 'order.') -> YES
    # - '#.critical' (ends with '.critical') -> YES
    # - 'europe.#' (starts with 'europe.') -> NO
    payload_a = {"alert": "EU order processor timeout", "code": "CRIT_01"}
    await MessageBrokerService.publish_topic_message(
        routing_key="order.eu.critical",
        payload=payload_a,
    )

    msg_order = await MessageBrokerService.consume_next_message(QUEUE_ORDER_MONITORING)
    assert msg_order is not None
    assert msg_order["payload"] == payload_a

    msg_crit = await MessageBrokerService.consume_next_message(QUEUE_CRITICAL_ALERTS)
    assert msg_crit is not None
    assert msg_crit["payload"] == payload_a

    msg_eu = await MessageBrokerService.consume_next_message(QUEUE_EU_LOGS)
    assert msg_eu is None, "QUEUE_EU_LOGS should NOT have matched 'order.eu.critical'"

    # Case B: 'europe.frankfurt.db.critical'
    # Should match:
    # - 'europe.#' -> YES
    # - '#.critical' -> YES
    # - 'order.*.*' -> NO
    payload_b = {"db": "postgres-primary", "error": "Disk 95% full"}
    await MessageBrokerService.publish_topic_message(
        routing_key="europe.frankfurt.db.critical",
        payload=payload_b,
    )

    assert (await MessageBrokerService.consume_next_message(QUEUE_EU_LOGS)) is not None
    assert (await MessageBrokerService.consume_next_message(QUEUE_CRITICAL_ALERTS)) is not None
    assert (await MessageBrokerService.consume_next_message(QUEUE_ORDER_MONITORING)) is None


@pytest.mark.asyncio
async def test_manual_ack_nack_requeue_lifecycle() -> None:
    """Verify unacknowledged messages can be negative-acknowledged and requeued."""
    topology = await MessageBrokerService.get_or_declare_topology()
    queue: MockAMQPQueue = topology.queues[QUEUE_ORDER_CREATED]

    payload = {"task": "financial_settlement", "amount": 5000}
    await MessageBrokerService.publish_direct_message(
        routing_key=ROUTING_KEY_ORDER_CREATED,
        payload=payload,
    )

    # 1. Fetch message with manual ACK (no auto_ack)
    msg = await queue.get(no_ack=False)
    assert msg is not None
    assert msg.acknowledged is False

    # 2. Simulate worker exception and negative-ack with requeue=True
    await msg.nack(requeue=True)
    assert msg.requeued is True

    # 3. Message should be back at head of the queue
    re_fetched = await queue.get(no_ack=False)
    assert re_fetched is not None
    assert re_fetched.decode_json() == payload

    # 4. Now acknowledge
    await re_fetched.ack()
    assert re_fetched.acknowledged is True
    assert await queue.get(no_ack=False) is None


@pytest.mark.asyncio
async def test_api_publish_direct_endpoint() -> None:
    """Verify POST /broker/publish/direct endpoint publishes and returns HTTP 202."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/broker/publish/direct",
            json={
                "routing_key": "order.created",
                "payload": {"order_id": "api_ord_1", "total": 99.99},
            },
        )
        assert response.status_code == 202
        body = response.json()
        assert body["status"] == "PUBLISHED"
        assert body["exchange"] == "orders.direct"
        assert body["routing_key"] == "order.created"
        assert body["delivery_mode"] == "PERSISTENT"

        # Consume via API
        consume_res = await client.post(f"/broker/consume/{QUEUE_ORDER_CREATED}")
        assert consume_res.status_code == 200
        c_body = consume_res.json()
        assert c_body["status"] == "CONSUMED"
        assert c_body["payload"]["order_id"] == "api_ord_1"
        assert c_body["acknowledged"] is True


@pytest.mark.asyncio
async def test_api_publish_fanout_endpoint() -> None:
    """Verify POST /broker/publish/fanout broadcasts event returning HTTP 202."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/broker/publish/fanout",
            json={
                "payload": {"global_alert": "Maintenance scheduled in 10 minutes"},
            },
        )
        assert response.status_code == 202
        body = response.json()
        assert body["status"] == "PUBLISHED"
        assert body["exchange"] == "events.fanout"
        assert body["delivery_mode"] == "PERSISTENT"

        # All 3 fanout queues can consume via API
        for q in [QUEUE_NOTIFICATION, QUEUE_ANALYTICS, QUEUE_AUDIT]:
            c_res = await client.post(f"/broker/consume/{q}")
            assert c_res.status_code == 200
            assert c_res.json()["payload"]["global_alert"] == "Maintenance scheduled in 10 minutes"


@pytest.mark.asyncio
async def test_api_publish_topic_endpoint() -> None:
    """Verify POST /broker/publish/topic delivers to wildcard matched queue."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/broker/publish/topic",
            json={
                "routing_key": "order.inventory.critical",
                "payload": {"sku": "LAPTOP-01", "stock": 0},
            },
        )
        assert response.status_code == 202
        body = response.json()
        assert body["status"] == "PUBLISHED"
        assert body["exchange"] == "logs.topic"
        assert body["routing_key"] == "order.inventory.critical"

        # Should match QUEUE_CRITICAL_ALERTS ('#.critical')
        c_crit = await client.post(f"/broker/consume/{QUEUE_CRITICAL_ALERTS}")
        assert c_crit.status_code == 200
        assert c_crit.json()["payload"]["sku"] == "LAPTOP-01"

        # Should match QUEUE_ORDER_MONITORING ('order.*.*')
        c_ord = await client.post(f"/broker/consume/{QUEUE_ORDER_MONITORING}")
        assert c_ord.status_code == 200
        assert c_ord.json()["payload"]["sku"] == "LAPTOP-01"


@pytest.mark.asyncio
async def test_api_consume_empty_queue_404() -> None:
    """Verify POST /broker/consume/{queue_name} returns 404 when queue has no messages."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(f"/broker/consume/{QUEUE_ORDER_CANCELLED}")
        assert response.status_code == 404
        assert "No messages available" in response.json()["detail"]
