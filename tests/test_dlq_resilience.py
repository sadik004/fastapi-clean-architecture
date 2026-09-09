"""Comprehensive test suite for Day 57: Dead Letter Queue (DLQ) & Poison Message Isolation.

Verifies:
  1. Exponential Retry Schedule: Transient failures retry with backoff and succeed before DLQ.
  2. Poison Pill Quarantine: Unfixable messages exhaust retries, route to DLQ, and ACK primary stream.
  3. Forensic Envelope Verification: Metadata, traceback, error message, and timestamp preserved.
  4. Redrive Verification: Operational re-injection drains DLQ and returns messages to primary pipeline.
  5. HTTP Management Endpoints: GET /dlq/messages, POST /dlq/redrive, and POST /dlq/purge.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.schemas.dlq import DLQEnvelope
from app.services.dlq_service import DLQService


@pytest.fixture(autouse=True)
def clean_dlq_quarantine() -> None:
    """Clear quarantined messages between tests."""
    DLQService.clear_quarantine()


@pytest.mark.asyncio
async def test_exponential_retry_transient_recovery() -> None:
    """Verify transient failures retry with exponential backoff and succeed without dead-lettering."""
    attempts = 0
    ack_called = False

    async def flaky_handler(payload: dict[str, object]) -> str:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise ConnectionResetError("Transient network drop during processing")
        return f"Processed order: {payload.get('order_id')}"

    async def mock_ack() -> None:
        nonlocal ack_called
        ack_called = True

    payload = {"order_id": "ord_transient_001", "amount": 150.0}

    success, result = await DLQService.process_with_dlq(
        destination="orders.events",
        payload=payload,
        handler_func=flaky_handler,
        ack_func=mock_ack,
        message_id="msg_test_001",
        base_delay=0.01,  # Short base delay for testing speed
        max_retries=3,
    )

    assert success is True
    assert result == "Processed order: ord_transient_001"
    assert attempts == 3
    assert ack_called is True
    assert len(DLQService.get_quarantined_messages()) == 0


@pytest.mark.asyncio
async def test_poison_pill_quarantine_and_primary_queue_unblock() -> None:
    """Verify permanent poison pill exhausts retries, is routed to DLQ, and unblocks primary queue."""
    attempts = 0
    ack_called = False

    async def poison_handler(payload: dict[str, object]) -> None:
        nonlocal attempts
        attempts += 1
        raise ValueError(f"Poison pill detected: invalid schema in payload {payload}")

    async def mock_ack() -> None:
        nonlocal ack_called
        ack_called = True

    payload = {"corrupted_field": "invalid_binary_data_###"}

    success, result = await DLQService.process_with_dlq(
        destination="orders.events",
        payload=payload,
        handler_func=poison_handler,
        ack_func=mock_ack,
        message_id="msg_poison_002",
        base_delay=0.01,
        max_retries=3,
    )

    assert success is False
    assert isinstance(result, DLQEnvelope)
    assert attempts == 3
    # CRITICAL INVARIANT: The primary message MUST be ACKed/committed upon DLQ quarantine!
    assert ack_called is True

    # Quarantined DLQ inspection
    quarantined = DLQService.get_quarantined_messages()
    assert len(quarantined) == 1
    envelope = quarantined[0]
    assert envelope.message_id == "msg_poison_002"
    assert envelope.original_topic_or_queue == "orders.events"
    assert envelope.dlq_destination == "orders.dlq"
    assert envelope.retry_count == 3
    assert "Poison pill detected" in envelope.error_message


@pytest.mark.asyncio
async def test_forensic_envelope_integrity() -> None:
    """Verify forensic envelope captures exact stack trace, error, unmutated payload, and timestamp."""
    async def failing_step(payload: dict[str, object]) -> None:
        raise KeyError("missing_required_attribute_xyz")

    payload = {"user_id": 999, "missing": "nothing"}

    success, envelope = await DLQService.process_with_dlq(
        destination="payments.events",
        payload=payload,
        handler_func=failing_step,
        message_id="msg_forensic_003",
        base_delay=0.005,
        max_retries=2,
    )

    assert success is False
    assert isinstance(envelope, DLQEnvelope)
    assert envelope.payload == payload
    assert "missing_required_attribute_xyz" in envelope.error_message
    assert "KeyError" in envelope.error_traceback
    assert "failing_step" in envelope.error_traceback
    assert envelope.retry_count == 2
    assert envelope.failed_at is not None


@pytest.mark.asyncio
async def test_redrive_operational_recovery() -> None:
    """Verify redriving quarantined messages re-injects them to primary queue and clears DLQ."""
    # 1. Populate DLQ with 3 poison messages
    for i in range(1, 4):
        env = DLQEnvelope(
            message_id=f"msg_redrive_{i}",
            original_topic_or_queue="orders.events",
            payload={"order_id": f"ord_failed_{i}"},
            error_message="Downstream service timeout",
            error_traceback="Traceback...",
            retry_count=3,
        )
        await DLQService.route_to_dlq(env)

    assert len(DLQService.get_quarantined_messages()) == 3

    # 2. Re-inject into primary queue
    republished_destinations: list[str] = []
    republished_payloads: list[dict[str, object]] = []

    async def mock_republish(dest: str, data: dict[str, object]) -> None:
        republished_destinations.append(dest)
        republished_payloads.append(data)

    redriven_count = await DLQService.redrive_messages(
        queue_or_topic="orders.events",
        limit=10,
        republish_func=mock_republish,
    )

    assert redriven_count == 3
    assert len(republished_destinations) == 3
    assert all(d == "orders.events" for d in republished_destinations)
    assert len(DLQService.get_quarantined_messages()) == 0


def test_api_dlq_management_endpoints() -> None:
    """Verify HTTP endpoints: GET /dlq/messages, POST /dlq/redrive, and POST /dlq/purge."""
    client = TestClient(app)

    # 1. Manually route 2 messages into DLQ
    DLQService.clear_quarantine()
    for i in range(1, 3):
        env = DLQEnvelope(
            message_id=f"api_msg_{i}",
            original_topic_or_queue="orders.events",
            payload={"id": i},
            error_message="HTTP 500 from payment gateway",
            error_traceback="Stack trace...",
            retry_count=3,
        )
        import asyncio
        asyncio.run(DLQService.route_to_dlq(env))

    # 2. GET /dlq/messages
    res_list = client.get("/dlq/messages")
    assert res_list.status_code == 200
    data_list = res_list.json()
    assert data_list["total_quarantined"] == 2
    assert len(data_list["messages"]) == 2

    # 3. POST /dlq/redrive
    res_redrive = client.post(
        "/dlq/redrive",
        json={"queue_or_topic": "orders.events", "limit": 10},
    )
    assert res_redrive.status_code == 200
    assert res_redrive.json()["redriven_count"] == 2

    # 4. Confirm DLQ is empty after redrive
    res_empty = client.get("/dlq/messages")
    assert res_empty.json()["total_quarantined"] == 0

    # 5. Route 1 more message and test POST /dlq/purge
    env2 = DLQEnvelope(
        message_id="api_msg_purge_test",
        original_topic_or_queue="notifications.queue",
        payload={"alert": "critical"},
        error_message="SMS gateway dropped connection",
        error_traceback="Traceback...",
        retry_count=3,
    )
    import asyncio
    asyncio.run(DLQService.route_to_dlq(env2))

    res_purge = client.post("/dlq/purge", params={"queue_or_topic": "notifications.queue"})
    assert res_purge.status_code == 200
    assert res_purge.json()["purged_count"] == 1
    assert len(DLQService.get_quarantined_messages()) == 0
