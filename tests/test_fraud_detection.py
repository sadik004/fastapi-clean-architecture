"""Comprehensive Test Suite for Day 88: Real-Time Fraud Detection & Anomaly Velocity Engine Architecture.

Validates:
1. Low-Risk Clean Transfer: Standard $50 transfer returns risk_score == 0 and decision == "APPROVED".
2. Velocity Burst Rejection: 3 rapid transfers; 4th transfer trips velocity burst & spike, raising FraudDetectedException (HTTP 403).
3. High-Value Spike Rule: Single $60,000 transfer triggers HIGH_VALUE_SPIKE (+30 points) with FLAGGED_FOR_REVIEW.
4. Blacklisted Destination Immediate Abort: Attempting transfer to blacklisted account triggers DESTINATION_ACCOUNT_BLACKLISTED (+100 points) and HTTP 403 abort.
5. Flagged for Review Ledger Integration: Moderate risk transfer commits journal entry tagged with is_flagged == True.
6. Fraud Management REST Endpoints: Diagnostic simulation, velocity inspection, and blacklist admin endpoints.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import fakeredis.aioredis
import httpx
import pytest

from app.core.dependencies import get_fraud_detection_service, get_uow
from app.core.exceptions import FraudDetectedException
from app.core.redis import get_redis
from app.core.unit_of_work import InMemoryUnitOfWork
from app.main import app
from app.schemas.ledger import (
    AccountType,
    PostingCreateDTO,
    PostingDirection,
)
from app.services.fraud_detection_service import FraudDetectionService
from app.services.ledger_transfer_service import LedgerTransferService


@pytest.fixture
def fake_redis() -> fakeredis.aioredis.FakeRedis:
    """Fixture providing a fresh in-memory FakeRedis instance for fraud testing."""
    return fakeredis.aioredis.FakeRedis(decode_responses=True)


@pytest.fixture
def fraud_service(fake_redis: fakeredis.aioredis.FakeRedis) -> FraudDetectionService:
    """Fixture providing an active FraudDetectionService wired to fake_redis."""
    return FraudDetectionService(redis=fake_redis)


@pytest.fixture
def in_memory_uow() -> InMemoryUnitOfWork:
    """Fixture providing an isolated in-memory Unit of Work."""
    return InMemoryUnitOfWork()


@pytest.mark.asyncio
async def test_low_risk_clean_transfer(fraud_service: FraudDetectionService) -> None:
    """Test 1: Standard low-value transfer results in risk_score == 0 and decision == APPROVED."""
    source_id = uuid.uuid4()
    dest_id = uuid.uuid4()
    amount = Decimal("50.0000")

    result = await fraud_service.evaluate_transfer(
        source_account_id=source_id,
        destination_account_id=dest_id,
        amount=amount,
        currency="USD",
    )

    assert result.risk_score == 0
    assert result.decision == "APPROVED"
    assert len(result.violated_rules) == 0


@pytest.mark.asyncio
async def test_high_value_spike_rule(fraud_service: FraudDetectionService) -> None:
    """Test 3: Single transfer of $60,000 trips HIGH_VALUE_SPIKE rule (+30 points)."""
    source_id = uuid.uuid4()
    dest_id = uuid.uuid4()
    amount = Decimal("60000.0000")

    result = await fraud_service.evaluate_transfer(
        source_account_id=source_id,
        destination_account_id=dest_id,
        amount=amount,
        currency="USD",
    )

    assert result.risk_score == 30
    assert result.decision == "FLAGGED_FOR_REVIEW"
    assert "HIGH_VALUE_SPIKE" in result.violated_rules


@pytest.mark.asyncio
async def test_velocity_burst_rejection(
    fraud_service: FraudDetectionService,
    fake_redis: fakeredis.aioredis.FakeRedis,
    in_memory_uow: InMemoryUnitOfWork,
) -> None:
    """Test 2: 3 rapid transfers followed by a high-value 4th transfer trips velocity burst & spike, raising FraudDetectedException."""
    service = LedgerTransferService(
        uow=in_memory_uow,
        redis=fake_redis,
        fraud_service=fraud_service,
    )

    # 1. Setup accounts and seed $200,000 funding
    src = await in_memory_uow.ledger.create_account(
        account_number=f"src-{uuid.uuid4().hex[:8]}",
        name="Velocity Source Account",
        account_type=AccountType.ASSET,
        currency="USD",
    )
    dst = await in_memory_uow.ledger.create_account(
        account_number=f"dst-{uuid.uuid4().hex[:8]}",
        name="Velocity Destination Account",
        account_type=AccountType.ASSET,
        currency="USD",
    )
    vault = await in_memory_uow.ledger.create_account(
        account_number=f"vault-{uuid.uuid4().hex[:8]}",
        name="Vault",
        account_type=AccountType.LIABILITY,
        currency="USD",
    )
    await in_memory_uow.ledger.create_journal_entry(
        reference_id=f"fund-{uuid.uuid4().hex}",
        description="Seed funding",
        postings=[
            PostingCreateDTO(account_id=src.id, amount=Decimal("200000.0000"), direction=PostingDirection.DEBIT),
            PostingCreateDTO(account_id=vault.id, amount=Decimal("200000.0000"), direction=PostingDirection.CREDIT),
        ],
    )

    # 2. Execute 3 rapid transfers of $100 each (all should be APPROVED)
    for i in range(3):
        res = await service.transfer_funds(
            source_account_id=src.id,
            destination_account_id=dst.id,
            amount=Decimal("100.0000"),
            reference_id=f"rapid-{i}-{uuid.uuid4().hex[:8]}",
            description=f"Rapid transfer #{i + 1}",
        )
        assert res.transferred_amount == Decimal("100.0000")

    # Verify sliding-window count is now 3
    count, vol = await fraud_service.get_account_velocity(src.id)
    assert count == 3
    assert vol == Decimal("300.0000")

    # 3. Attempt 4th transfer with $60,000:
    # Rule 1 (count >= 3) adds +40 (VELOCITY_BURST_EXCEEDED)
    # Rule 2 (amount >= 50,000) adds +30 (HIGH_VALUE_SPIKE)
    # Total risk score = 70 -> REJECTED!
    with pytest.raises(FraudDetectedException) as exc_info:
        await service.transfer_funds(
            source_account_id=src.id,
            destination_account_id=dst.id,
            amount=Decimal("60000.0000"),
            reference_id=f"rapid-4-{uuid.uuid4().hex[:8]}",
            description="Exploitative high-value spike attempt",
        )

    assert exc_info.value.risk_score >= 70
    assert "VELOCITY_BURST_EXCEEDED" in exc_info.value.reasons
    assert "HIGH_VALUE_SPIKE" in exc_info.value.reasons


@pytest.mark.asyncio
async def test_blacklisted_destination_immediate_abort(
    fraud_service: FraudDetectionService,
    fake_redis: fakeredis.aioredis.FakeRedis,
    in_memory_uow: InMemoryUnitOfWork,
) -> None:
    """Test 4: Attempting transfer to blacklisted account triggers DESTINATION_ACCOUNT_BLACKLISTED (+100 points) and immediate abort."""
    service = LedgerTransferService(
        uow=in_memory_uow,
        redis=fake_redis,
        fraud_service=fraud_service,
    )

    src = await in_memory_uow.ledger.create_account(
        account_number=f"src-{uuid.uuid4().hex[:8]}",
        name="Victim Account",
        account_type=AccountType.ASSET,
        currency="USD",
    )
    blacklisted_dst = await in_memory_uow.ledger.create_account(
        account_number=f"dst-mule-{uuid.uuid4().hex[:8]}",
        name="Sanctioned Mule Account",
        account_type=AccountType.ASSET,
        currency="USD",
    )
    vault = await in_memory_uow.ledger.create_account(
        account_number=f"vault-{uuid.uuid4().hex[:8]}",
        name="Vault",
        account_type=AccountType.LIABILITY,
        currency="USD",
    )
    await in_memory_uow.ledger.create_journal_entry(
        reference_id=f"fund-{uuid.uuid4().hex}",
        description="Seed funding",
        postings=[
            PostingCreateDTO(account_id=src.id, amount=Decimal("1000.0000"), direction=PostingDirection.DEBIT),
            PostingCreateDTO(account_id=vault.id, amount=Decimal("1000.0000"), direction=PostingDirection.CREDIT),
        ],
    )

    # Blacklist destination account
    await fraud_service.blacklist_account(blacklisted_dst.id, reason="Sanctioned entity list match")
    assert await fraud_service.is_account_blacklisted(blacklisted_dst.id) is True

    # Attempt transfer to blacklisted account
    with pytest.raises(FraudDetectedException) as exc_info:
        await service.transfer_funds(
            source_account_id=src.id,
            destination_account_id=blacklisted_dst.id,
            amount=Decimal("50.0000"),
            reference_id=f"tx-mule-{uuid.uuid4().hex[:8]}",
            description="Transfer to sanctioned destination",
        )

    assert exc_info.value.risk_score >= 100
    assert "DESTINATION_ACCOUNT_BLACKLISTED" in exc_info.value.reasons


@pytest.mark.asyncio
async def test_flagged_for_review_journal_entry(
    fraud_service: FraudDetectionService,
    fake_redis: fakeredis.aioredis.FakeRedis,
    in_memory_uow: InMemoryUnitOfWork,
) -> None:
    """Test 5: Moderate risk transfer (score 30) succeeds but commits journal entry tagged with is_flagged == True."""
    service = LedgerTransferService(
        uow=in_memory_uow,
        redis=fake_redis,
        fraud_service=fraud_service,
    )

    src = await in_memory_uow.ledger.create_account(
        account_number=f"src-{uuid.uuid4().hex[:8]}",
        name="Whale Account",
        account_type=AccountType.ASSET,
        currency="USD",
    )
    dst = await in_memory_uow.ledger.create_account(
        account_number=f"dst-{uuid.uuid4().hex[:8]}",
        name="Beneficiary Account",
        account_type=AccountType.ASSET,
        currency="USD",
    )
    vault = await in_memory_uow.ledger.create_account(
        account_number=f"vault-{uuid.uuid4().hex[:8]}",
        name="Vault",
        account_type=AccountType.LIABILITY,
        currency="USD",
    )
    await in_memory_uow.ledger.create_journal_entry(
        reference_id=f"fund-{uuid.uuid4().hex}",
        description="Seed funding",
        postings=[
            PostingCreateDTO(account_id=src.id, amount=Decimal("100000.0000"), direction=PostingDirection.DEBIT),
            PostingCreateDTO(account_id=vault.id, amount=Decimal("100000.0000"), direction=PostingDirection.CREDIT),
        ],
    )

    # Single transfer of $60,000 (HIGH_VALUE_SPIKE -> score 30 -> FLAGGED_FOR_REVIEW)
    ref_id = f"tx-flagged-{uuid.uuid4().hex[:8]}"
    response = await service.transfer_funds(
        source_account_id=src.id,
        destination_account_id=dst.id,
        amount=Decimal("60000.0000"),
        reference_id=ref_id,
        description="Legitimate high-value business acquisition",
    )

    # Response should reflect is_flagged == True
    assert response.is_flagged is True

    # Journal entry in repository must also have is_flagged == True
    saved_entry = await in_memory_uow.ledger.get_journal_entry_by_reference(ref_id)
    assert saved_entry is not None
    assert saved_entry.is_flagged is True


@pytest.mark.asyncio
async def test_fraud_management_api_endpoints(
    fake_redis: fakeredis.aioredis.FakeRedis,
    in_memory_uow: InMemoryUnitOfWork,
) -> None:
    """Test 6: REST API endpoints for blacklist management, velocity inspection, and simulation evaluation."""
    test_fraud_service = FraudDetectionService(redis=fake_redis)

    app.dependency_overrides[get_redis] = lambda: fake_redis
    app.dependency_overrides[get_uow] = lambda: in_memory_uow
    app.dependency_overrides[get_fraud_detection_service] = lambda: test_fraud_service

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        test_acc_id = uuid.uuid4()

        # 1. Test POST /api/v1/ledger/fraud/blacklist
        blacklist_resp = await client.post(
            "/api/v1/ledger/fraud/blacklist",
            json={
                "account_id": str(test_acc_id),
                "reason": "Suspected mule account",
            },
        )
        assert blacklist_resp.status_code == 200
        bl_data = blacklist_resp.json()
        assert bl_data["is_blacklisted"] is True
        assert bl_data["account_id"] == str(test_acc_id)

        # 2. Test GET /api/v1/ledger/fraud/velocity/{account_id}
        vel_resp = await client.get(f"/api/v1/ledger/fraud/velocity/{test_acc_id}")
        assert vel_resp.status_code == 200
        vel_data = vel_resp.json()
        assert vel_data["transfer_count"] == 0
        assert Decimal(str(vel_data["cumulative_amount"])) == Decimal("0.0000")

        # 3. Test POST /api/v1/ledger/fraud/evaluate
        eval_resp = await client.post(
            "/api/v1/ledger/fraud/evaluate",
            json={
                "source_account_id": str(uuid.uuid4()),
                "destination_account_id": str(test_acc_id),  # Blacklisted!
                "amount": "100.0000",
                "currency": "USD",
            },
        )
        assert eval_resp.status_code == 200
        eval_data = eval_resp.json()
        assert eval_data["risk_score"] >= 100
        assert eval_data["decision"] == "REJECTED"
        assert "DESTINATION_ACCOUNT_BLACKLISTED" in eval_data["violated_rules"]

    app.dependency_overrides.clear()
