"""Comprehensive Test Suite for Day 87: Real-Time Ledger Audit Streams via Kafka & Transactional Outbox Relay.

Verifies:
1. Atomic Outbox Co-Location: Ledger mutations and Outbox events persist atomically within the same Unit of Work.
2. Complete Rollback Parity: On InsufficientFundsException or balance violation, both postings and outbox events roll back.
3. Strict Partition Key Ordering: Kafka partition key is strictly bound to source_account_id (FIFO account ordering).
4. Decimal Float-Safety: Financial amounts in JSON payloads are serialized strictly as arbitrary-precision strings.
5. Ledger Outbox Relay Dispatch: Dispatches pending events to Kafka and transitions status to 'PROCESSED'.
6. Kafka Resilience & Retry Tracking: Broker dispatches that fail preserve 'PENDING' status and increment retry_count.
7. Telemetry & Diagnostic Endpoints: POST /api/v1/ledger/outbox/relay and GET /api/v1/ledger/outbox/pending.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from unittest.mock import AsyncMock

import httpx
import pytest

from app.core.exceptions import InsufficientFundsException
from app.core.kafka import MockAIOKafkaProducer
from app.core.unit_of_work import InMemoryUnitOfWork
from app.main import app
from app.schemas.ledger import AccountType, PostingCreateDTO, PostingDirection
from app.schemas.ledger_events import LedgerTransferCompletedEvent
from app.services.fx_conversion_service import FXConversionService
from app.services.ledger_outbox_relay_service import LedgerOutboxRelayService
from app.services.ledger_transfer_service import LedgerTransferService


@pytest.fixture
def in_memory_uow() -> InMemoryUnitOfWork:
    """Provide a fresh, isolated in-memory Unit of Work."""
    return InMemoryUnitOfWork()


@pytest.fixture
def mock_kafka_producer() -> MockAIOKafkaProducer:
    """Provide a fresh MockAIOKafkaProducer for isolated Kafka testing."""
    producer = MockAIOKafkaProducer(partitions_per_topic=3)
    producer.clear()
    return producer


@pytest.mark.asyncio
async def test_atomic_outbox_colocation(in_memory_uow: InMemoryUnitOfWork) -> None:
    """Test 1: Assert ledger entry and OutboxEventModel are persisted atomically within the same transaction."""
    fx_service = FXConversionService()
    transfer_service = LedgerTransferService(uow=in_memory_uow, fx_service=fx_service)

    # 1. Setup Alice (USD Asset) and Bob (USD Asset) accounts
    async with in_memory_uow as uow:
        alice = await uow.ledger.create_account(
            account_number="acc-alice-outbox-01",
            name="Alice Outbox Wallet",
            account_type=AccountType.ASSET,
            currency="USD",
        )
        bob = await uow.ledger.create_account(
            account_number="acc-bob-outbox-01",
            name="Bob Outbox Wallet",
            account_type=AccountType.ASSET,
            currency="USD",
        )
        treasury = await uow.ledger.create_account(
            account_number="acc-treasury-outbox-01",
            name="Treasury Reserve",
            account_type=AccountType.LIABILITY,
            currency="USD",
        )

        # Seed Alice with 500.0000 USD
        await uow.ledger.create_journal_entry(
            reference_id=f"seed-{uuid.uuid4().hex[:8]}",
            description="Initial deposit to Alice",
            postings=[
                PostingCreateDTO(account_id=alice.id, amount=Decimal("500.0000"), direction=PostingDirection.DEBIT),
                PostingCreateDTO(account_id=treasury.id, amount=Decimal("500.0000"), direction=PostingDirection.CREDIT),
            ],
        )
        await uow.commit()

    initial_outbox_count = len(in_memory_uow.outbox._store)

    # 2. Execute Transfer: Alice -> Bob 150.0000 USD with 2.5000 USD fee
    ref_id = f"tx-outbox-{uuid.uuid4().hex[:8]}"
    result = await transfer_service.transfer_funds(
        source_account_id=alice.id,
        destination_account_id=bob.id,
        amount=Decimal("150.0000"),
        fee_amount=Decimal("2.5000"),
        fee_account_id=treasury.id,
        reference_id=ref_id,
        description="P2P payment with outbox audit stream",
    )

    # 3. Assert Journal Entry and Balances
    assert result.reference_id == ref_id
    assert result.transferred_amount == Decimal("150.0000")
    assert result.source_new_balance == Decimal("347.5000")
    assert result.destination_new_balance == Decimal("150.0000")

    # 4. Assert Outbox Event Co-Location
    assert len(in_memory_uow.outbox._store) == initial_outbox_count + 1
    events = list(in_memory_uow.outbox._store.values())
    latest_event = events[-1]

    assert latest_event.topic == "ledger.transfers.v1"
    assert latest_event.event_type == "ledger.transfer.completed.v1"
    assert latest_event.partition_key == str(alice.id)
    assert latest_event.status == "PENDING"
    assert latest_event.retry_count == 0
    assert latest_event.published_at is None

    # Verify Decimal Float-Safety in JSON Payload
    payload = latest_event.payload
    assert payload["reference_id"] == ref_id
    assert payload["source_account_id"] == str(alice.id)
    assert payload["destination_account_id"] == str(bob.id)
    assert payload["amount"] == "150.0000"  # Strict string representation, zero float!
    assert isinstance(payload["amount"], str)
    assert payload["fee_amount"] == "2.5000"  # Strict string representation
    assert isinstance(payload["fee_amount"], str)
    assert payload["currency"] == "USD"
    assert payload["partition_key"] == str(alice.id)


@pytest.mark.asyncio
async def test_outbox_rollback_parity(in_memory_uow: InMemoryUnitOfWork) -> None:
    """Test 2: When transfer fails (e.g. InsufficientFunds), both ledger postings and outbox events roll back."""
    fx_service = FXConversionService()
    transfer_service = LedgerTransferService(uow=in_memory_uow, fx_service=fx_service)

    async with in_memory_uow as uow:
        alice = await uow.ledger.create_account(
            account_number="acc-alice-broke-01",
            name="Alice Broke Wallet",
            account_type=AccountType.ASSET,
            currency="USD",
        )
        bob = await uow.ledger.create_account(
            account_number="acc-bob-broke-01",
            name="Bob Recipient",
            account_type=AccountType.ASSET,
            currency="USD",
        )
        await uow.commit()

    initial_outbox_count = len(in_memory_uow.outbox._store)
    initial_entries_count = len(in_memory_uow.ledger._entries)

    # Alice has 0 balance, attempts to transfer 100 USD
    ref_id = f"tx-fail-{uuid.uuid4().hex[:8]}"
    with pytest.raises(InsufficientFundsException):
        await transfer_service.transfer_funds(
            source_account_id=alice.id,
            destination_account_id=bob.id,
            amount=Decimal("100.0000"),
            reference_id=ref_id,
            description="Attempted overdraft transfer",
        )

    # Invariant: 100% rollback parity (Zero phantom ledger entries, Zero orphaned outbox events)
    assert len(in_memory_uow.outbox._store) == initial_outbox_count
    assert len(in_memory_uow.ledger._entries) == initial_entries_count


@pytest.mark.asyncio
async def test_ledger_outbox_relay_dispatch(
    in_memory_uow: InMemoryUnitOfWork,
    mock_kafka_producer: MockAIOKafkaProducer,
) -> None:
    """Test 3: LedgerOutboxRelayService dispatches events to Kafka with account partition key and marks PROCESSED."""
    await mock_kafka_producer.start()

    # 1. Seed pending outbox events
    alice_id = str(uuid.uuid4())
    bob_id = str(uuid.uuid4())
    event_schema = LedgerTransferCompletedEvent.create(
        reference_id="ref-audit-100",
        source_account_id=alice_id,
        destination_account_id=bob_id,
        amount=Decimal("250.7500"),
        currency="USD",
        fee_amount=Decimal("1.2500"),
    )

    async with in_memory_uow as uow:
        await uow.outbox.record_event(
            event_type=event_schema.event_type,
            aggregate_type="ledger.transfers.v1",
            aggregate_id=alice_id,
            payload=event_schema.model_dump(),
        )
        await uow.commit()

    pending_pre = await in_memory_uow.outbox.get_pending_events()
    assert len(pending_pre) == 1
    outbox_id = pending_pre[0].id

    # 2. Run LedgerOutboxRelayService
    relay_service = LedgerOutboxRelayService(
        uow=in_memory_uow,
        kafka_producer=mock_kafka_producer,
    )
    dispatched = await relay_service.publish_pending_events(batch_size=10)
    assert dispatched == 1

    # 3. Assert Outbox status transitioned to PROCESSED
    stored_event = await in_memory_uow.outbox.get_by_id(outbox_id)
    assert stored_event is not None
    assert stored_event.status == "PROCESSED"
    assert stored_event.published_at is not None

    # 4. Assert Kafka message received on partition hashed from alice_id
    partition_id = mock_kafka_producer._determine_partition("ledger.transfers.v1", alice_id.encode("utf-8"))
    messages = mock_kafka_producer.get_partition_messages("ledger.transfers.v1", partition_id)
    assert len(messages) >= 1

    dispatched_msg = messages[-1]
    assert dispatched_msg["key"] == alice_id
    assert dispatched_msg["value"]["reference_id"] == "ref-audit-100"
    assert dispatched_msg["value"]["amount"] == "250.7500"
    assert dispatched_msg["value"]["partition_key"] == alice_id


@pytest.mark.asyncio
async def test_ledger_outbox_relay_broker_failure_resilience(
    in_memory_uow: InMemoryUnitOfWork,
) -> None:
    """Test 4: When Kafka dispatch fails, event remains PENDING and retry_count is incremented."""
    failing_producer = AsyncMock()
    failing_producer.send_and_wait.side_effect = ConnectionError("Kafka cluster leader unavailable")

    alice_id = str(uuid.uuid4())
    event_schema = LedgerTransferCompletedEvent.create(
        reference_id="ref-fail-resilience",
        source_account_id=alice_id,
        destination_account_id=str(uuid.uuid4()),
        amount=Decimal("50.0000"),
        currency="USD",
    )

    async with in_memory_uow as uow:
        recorded = await uow.outbox.record_event(
            event_type=event_schema.event_type,
            aggregate_type="ledger.transfers.v1",
            aggregate_id=alice_id,
            payload=event_schema.model_dump(),
        )
        await uow.commit()

    relay_service = LedgerOutboxRelayService(uow=in_memory_uow, kafka_producer=failing_producer)
    dispatched = await relay_service.publish_pending_events(batch_size=10)

    # 0 events dispatched, but zero crash
    assert dispatched == 0

    # Event remains PENDING with incremented retry count
    record = await in_memory_uow.outbox.get_by_id(recorded.id)
    assert record is not None
    assert record.status == "PENDING"
    assert record.retry_count == 1
    assert record.published_at is None


@pytest.mark.asyncio
async def test_http_ledger_outbox_relay_and_pending_endpoints() -> None:
    """Test 5: HTTP endpoints for /api/v1/ledger/outbox/relay and /api/v1/ledger/outbox/pending."""
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        # 1. Inspect pending ledger outbox events
        pending_res = await client.get("/api/v1/ledger/outbox/pending?limit=20")
        assert pending_res.status_code == 200
        pending_data = pending_res.json()
        assert "total_pending" in pending_data
        assert "events" in pending_data
        assert isinstance(pending_data["events"], list)

        # 2. Trigger manual flush/relay
        relay_res = await client.post("/api/v1/ledger/outbox/relay?batch_size=20")
        assert relay_res.status_code == 200
        relay_data = relay_res.json()
        assert relay_data["status"] == "SUCCESS"
        assert "dispatched_count" in relay_data
        assert "message" in relay_data
