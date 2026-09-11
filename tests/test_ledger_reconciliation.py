"""Comprehensive Test Suite for Day 89: End-to-End Ledger Reconciliation & Drift Recovery Engine Architecture.

Validates:
1. 100% Matched Feed Test: Reconcile settlement matching ledger entries exactly; assert discrepancy_records == 0, status == COMPLETED.
2. Missing In Ledger Auto-Compensate Test: Submit feed with entry missing in ledger; assert MISSING_IN_LEDGER, automated compensating entry created, resolution_status == AUTO_COMPENSATED.
3. Amount Mismatch Detection Test: Submit feed with $100 external vs $95 internal; assert AMOUNT_MISMATCH is flagged for MANUAL_REVIEW.
4. Auto-Compensate Disabled Test: MISSING_IN_LEDGER remains UNRESOLVED when auto_compensate == False.
5. Idempotency Conflict Test: Replaying duplicate batch_reference raises DuplicateReferenceException.
6. HTTP REST API Endpoints: Verify POST /reconciliation/run and GET /reconciliation/batches/{id} end-to-end.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal

import httpx
import pytest

from app.core.dependencies import get_ledger_reconciliation_service, get_uow
from app.core.exceptions import DuplicateReferenceException
from app.core.unit_of_work import InMemoryUnitOfWork
from app.main import app
from app.schemas.ledger import (
    AccountType,
    PostingCreateDTO,
    PostingDirection,
)
from app.schemas.reconciliation import (
    DiscrepancyType,
    ResolutionStatus,
    SettlementItemDTO,
)
from app.services.ledger_reconciliation_service import LedgerReconciliationService


@pytest.fixture
def in_memory_uow() -> InMemoryUnitOfWork:
    """Fixture providing an isolated in-memory Unit of Work."""
    return InMemoryUnitOfWork()


@pytest.fixture
def reconciliation_service(in_memory_uow: InMemoryUnitOfWork) -> LedgerReconciliationService:
    """Fixture providing an active LedgerReconciliationService with in-memory UoW."""
    return LedgerReconciliationService(uow=in_memory_uow)


@pytest.mark.asyncio
async def test_100_percent_matched_feed(
    reconciliation_service: LedgerReconciliationService,
    in_memory_uow: InMemoryUnitOfWork,
) -> None:
    """Test 1: Reconcile settlement matching ledger entries exactly; assert discrepancy_records == 0."""
    async with in_memory_uow:
        acc1 = await in_memory_uow.ledger.create_account("ACC-USER-1", "User 1", AccountType.ASSET)
        acc2 = await in_memory_uow.ledger.create_account("ACC-MERCHANT-1", "Merchant 1", AccountType.LIABILITY)

        # Record 2 internal ledger transactions
        await in_memory_uow.ledger.create_journal_entry(
            reference_id="stripe-tx-101",
            description="Payment 101",
            postings=[
                PostingCreateDTO(account_id=acc1.id, amount=Decimal("100.0000"), direction=PostingDirection.DEBIT),
                PostingCreateDTO(account_id=acc2.id, amount=Decimal("100.0000"), direction=PostingDirection.CREDIT),
            ],
        )
        await in_memory_uow.ledger.create_journal_entry(
            reference_id="stripe-tx-102",
            description="Payment 102",
            postings=[
                PostingCreateDTO(account_id=acc1.id, amount=Decimal("250.5000"), direction=PostingDirection.DEBIT),
                PostingCreateDTO(account_id=acc2.id, amount=Decimal("250.5000"), direction=PostingDirection.CREDIT),
            ],
        )
        await in_memory_uow.commit()

    feed_items = [
        SettlementItemDTO(
            reference_id="stripe-tx-101",
            amount=Decimal("100.0000"),
            currency="USD",
            posted_at=datetime.now(UTC),
        ),
        SettlementItemDTO(
            reference_id="stripe-tx-102",
            amount=Decimal("250.5000"),
            currency="USD",
            posted_at=datetime.now(UTC),
        ),
    ]

    summary = await reconciliation_service.reconcile_settlement_feed(
        batch_reference="batch-stripe-20260911-01",
        gateway_name="STRIPE",
        settlement_items=feed_items,
        auto_compensate=True,
    )

    assert summary.total_records == 2
    assert summary.matched_records == 2
    assert summary.discrepancy_records == 0
    assert summary.auto_compensated_records == 0
    assert summary.status == "COMPLETED"
    assert len(summary.items) == 2
    for item in summary.items:
        assert item.discrepancy_type == DiscrepancyType.MATCHED
        assert item.resolution_status == ResolutionStatus.RESOLVED
        assert item.compensating_journal_entry_id is None


@pytest.mark.asyncio
async def test_missing_in_ledger_auto_compensate(
    reconciliation_service: LedgerReconciliationService,
    in_memory_uow: InMemoryUnitOfWork,
) -> None:
    """Test 2: External settlement entry missing in ledger triggers automated compensating 2-leg journal entry."""
    missing_item = SettlementItemDTO(
        reference_id="stripe-drift-999",
        amount=Decimal("450.0000"),
        currency="USD",
        posted_at=datetime.now(UTC),
    )

    summary = await reconciliation_service.reconcile_settlement_feed(
        batch_reference="batch-stripe-drift-01",
        gateway_name="STRIPE",
        settlement_items=[missing_item],
        auto_compensate=True,
    )

    assert summary.total_records == 1
    assert summary.matched_records == 0
    assert summary.discrepancy_records == 1
    assert summary.auto_compensated_records == 1

    reconciled_item = summary.items[0]
    assert reconciled_item.reference_id == "stripe-drift-999"
    assert reconciled_item.discrepancy_type == DiscrepancyType.MISSING_IN_LEDGER
    assert reconciled_item.resolution_status == ResolutionStatus.AUTO_COMPENSATED
    assert reconciled_item.compensating_journal_entry_id is not None

    # Verify compensating journal entry in persistence layer
    async with in_memory_uow:
        comp_entry = await in_memory_uow.ledger.get_journal_entry_by_id(reconciled_item.compensating_journal_entry_id)
        assert comp_entry is not None
        assert "stripe-drift-999" in comp_entry.description
        assert len(comp_entry.postings) == 2

        # Check zero-sum balance invariant on compensating entry
        debits = sum(p.amount for p in comp_entry.postings if p.direction == PostingDirection.DEBIT)
        credits = sum(p.amount for p in comp_entry.postings if p.direction == PostingDirection.CREDIT)
        assert debits == credits == Decimal("450.0000")

        # Verify account assignments
        user_settle_acc = await in_memory_uow.ledger.get_account_by_number("SETTLEMENT-USER-USD")
        gateway_clear_acc = await in_memory_uow.ledger.get_account_by_number("GATEWAY-CLEARING-STRIPE-USD")
        assert user_settle_acc is not None
        assert gateway_clear_acc is not None

        postings_by_acc = {p.account_id: p for p in comp_entry.postings}
        assert postings_by_acc[user_settle_acc.id].direction == PostingDirection.DEBIT
        assert postings_by_acc[gateway_clear_acc.id].direction == PostingDirection.CREDIT


@pytest.mark.asyncio
async def test_amount_mismatch_detection(
    reconciliation_service: LedgerReconciliationService,
    in_memory_uow: InMemoryUnitOfWork,
) -> None:
    """Test 3: External $100 vs internal $95 is flagged as AMOUNT_MISMATCH for manual review."""
    async with in_memory_uow:
        acc1 = await in_memory_uow.ledger.create_account("ACC-USER-2", "User 2", AccountType.ASSET)
        acc2 = await in_memory_uow.ledger.create_account("ACC-MERCHANT-2", "Merchant 2", AccountType.LIABILITY)

        await in_memory_uow.ledger.create_journal_entry(
            reference_id="stripe-mismatch-55",
            description="Partial payment",
            postings=[
                PostingCreateDTO(account_id=acc1.id, amount=Decimal("95.0000"), direction=PostingDirection.DEBIT),
                PostingCreateDTO(account_id=acc2.id, amount=Decimal("95.0000"), direction=PostingDirection.CREDIT),
            ],
        )
        await in_memory_uow.commit()

    feed_item = SettlementItemDTO(
        reference_id="stripe-mismatch-55",
        amount=Decimal("100.0000"),
        currency="USD",
        posted_at=datetime.now(UTC),
    )

    summary = await reconciliation_service.reconcile_settlement_feed(
        batch_reference="batch-stripe-mismatch-01",
        gateway_name="STRIPE",
        settlement_items=[feed_item],
        auto_compensate=True,
    )

    assert summary.total_records == 1
    assert summary.matched_records == 0
    assert summary.discrepancy_records == 1
    assert summary.auto_compensated_records == 0

    item = summary.items[0]
    assert item.reference_id == "stripe-mismatch-55"
    assert item.discrepancy_type == DiscrepancyType.AMOUNT_MISMATCH
    assert item.resolution_status == ResolutionStatus.MANUAL_REVIEW
    assert item.external_amount == Decimal("100.0000")
    assert item.internal_amount == Decimal("95.0000")
    assert item.compensating_journal_entry_id is None


@pytest.mark.asyncio
async def test_missing_in_ledger_without_auto_compensate(
    reconciliation_service: LedgerReconciliationService,
) -> None:
    """Test 4: When auto_compensate is False, missing records are marked UNRESOLVED with zero compensating entry."""
    missing_item = SettlementItemDTO(
        reference_id="bkash-no-auto-1",
        amount=Decimal("300.0000"),
        currency="BDT",
        posted_at=datetime.now(UTC),
    )

    summary = await reconciliation_service.reconcile_settlement_feed(
        batch_reference="batch-bkash-no-auto-01",
        gateway_name="BKASH",
        settlement_items=[missing_item],
        auto_compensate=False,
    )

    assert summary.total_records == 1
    assert summary.matched_records == 0
    assert summary.discrepancy_records == 1
    assert summary.auto_compensated_records == 0

    item = summary.items[0]
    assert item.discrepancy_type == DiscrepancyType.MISSING_IN_LEDGER
    assert item.resolution_status == ResolutionStatus.UNRESOLVED
    assert item.compensating_journal_entry_id is None


@pytest.mark.asyncio
async def test_duplicate_batch_reference_rejection(
    reconciliation_service: LedgerReconciliationService,
) -> None:
    """Test 5: Re-running reconciliation with the same batch_reference raises DuplicateReferenceException."""
    item = SettlementItemDTO(
        reference_id="tx-idempotent-1",
        amount=Decimal("75.0000"),
        currency="USD",
        posted_at=datetime.now(UTC),
    )

    await reconciliation_service.reconcile_settlement_feed(
        batch_reference="batch-unique-ref-01",
        gateway_name="STRIPE",
        settlement_items=[item],
        auto_compensate=False,
    )

    with pytest.raises(DuplicateReferenceException) as exc_info:
        await reconciliation_service.reconcile_settlement_feed(
            batch_reference="batch-unique-ref-01",
            gateway_name="STRIPE",
            settlement_items=[item],
            auto_compensate=False,
        )

    assert "batch-unique-ref-01" in str(exc_info.value)


@pytest.mark.asyncio
async def test_http_reconciliation_endpoints(
    in_memory_uow: InMemoryUnitOfWork,
) -> None:
    """Test 6: REST API endpoints POST /reconciliation/run and GET /reconciliation/batches/{id}."""
    test_service = LedgerReconciliationService(uow=in_memory_uow)

    app.dependency_overrides[get_uow] = lambda: in_memory_uow
    app.dependency_overrides[get_ledger_reconciliation_service] = lambda: test_service

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        batch_ref = f"api-batch-{uuid.uuid4().hex[:8]}"

        # 1. POST /api/v1/ledger/reconciliation/run
        run_payload = {
            "batch_reference": batch_ref,
            "gateway_name": "STRIPE",
            "settlement_items": [
                {
                    "reference_id": f"ref-api-{uuid.uuid4().hex[:6]}",
                    "amount": "150.0000",
                    "currency": "USD",
                }
            ],
            "auto_compensate": True,
        }

        resp = await client.post("/api/v1/ledger/reconciliation/run", json=run_payload)
        assert resp.status_code == 201
        data = resp.json()
        assert data["batch_reference"] == batch_ref
        assert data["total_records"] == 1
        assert data["discrepancy_records"] == 1
        assert data["auto_compensated_records"] == 1
        batch_id = data["batch_id"]

        # 2. GET /api/v1/ledger/reconciliation/batches/{batch_id}
        get_resp = await client.get(f"/api/v1/ledger/reconciliation/batches/{batch_id}")
        assert get_resp.status_code == 200
        get_data = get_resp.json()
        assert get_data["batch_id"] == batch_id
        assert get_data["batch_reference"] == batch_ref
        assert len(get_data["items"]) == 1
        assert get_data["items"][0]["resolution_status"] == "AUTO_COMPENSATED"

        # 3. GET non-existent batch returns 404
        non_existent_id = uuid.uuid4()
        not_found_resp = await client.get(f"/api/v1/ledger/reconciliation/batches/{non_existent_id}")
        assert not_found_resp.status_code == 404

    app.dependency_overrides.clear()
