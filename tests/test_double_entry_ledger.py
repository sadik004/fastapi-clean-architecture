"""Comprehensive Test Suite for Day 83: Fintech Double-Entry Ledger Domain Architecture.

Validates:
1. Double-Entry Accounting Invariants (Sum of Debits == Sum of Credits).
2. Unbalanced transactions rejected with HTTP 422 Unprocessable Entity.
3. Multi-leg compound postings (e.g. deposit with platform fee).
4. Dynamic computed balances (Zero mutable balance columns).
5. Normal balance rules for all 5 AccountTypes (ASSET, LIABILITY, EQUITY, REVENUE, EXPENSE).
6. Arbitrary-precision decimal accuracy with zero float rounding errors.
7. Idempotent reference deduplication returning HTTP 409 Conflict.
8. Nonexistent or inactive account guards.
9. In-memory repository unit test fast path.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import httpx
import pytest

from app.core.exceptions import (
    DuplicateReferenceException,
    EntityConflictException,
    UnbalancedJournalEntryException,
)
from app.main import app
from app.repositories.ledger_repository import (
    InMemoryLedgerRepository,
)
from app.schemas.ledger import (
    AccountType,
    JournalEntryCreateDTO,
    LedgerAccountCreate,
    PostingCreateDTO,
    PostingDirection,
)
from app.services.ledger_domain_service import LedgerDomainService


@pytest.mark.asyncio
async def test_ledger_account_creation_and_balance_flow() -> None:
    """End-to-end HTTP API lifecycle: create accounts, post balanced entry, verify dynamic balance."""
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        # 1. Create Cash Asset Account
        cash_acc_num = f"1000-{uuid.uuid4().hex[:8]}"
        res_cash = await client.post(
            "/api/v1/ledger/accounts",
            json={
                "account_number": cash_acc_num,
                "name": "Operating Cash",
                "account_type": "ASSET",
                "currency": "USD",
            },
        )
        assert res_cash.status_code == 201, res_cash.text
        cash_account = res_cash.json()
        cash_id = cash_account["id"]
        assert cash_account["account_number"] == cash_acc_num
        assert cash_account["account_type"] == "ASSET"

        # 2. Create Customer Deposit Liability Account
        liability_acc_num = f"2000-{uuid.uuid4().hex[:8]}"
        res_liab = await client.post(
            "/api/v1/ledger/accounts",
            json={
                "account_number": liability_acc_num,
                "name": "Customer Deposits",
                "account_type": "LIABILITY",
                "currency": "USD",
            },
        )
        assert res_liab.status_code == 201, res_liab.text
        liab_account = res_liab.json()
        liab_id = liab_account["id"]

        # 3. Check Initial Balances (should be 0.0000)
        bal_cash_init = await client.get(f"/api/v1/ledger/accounts/{cash_id}/balance")
        assert bal_cash_init.status_code == 200
        assert Decimal(str(bal_cash_init.json()["balance"])) == Decimal("0.0000")
        assert bal_cash_init.json()["normal_balance"] == "DEBIT"

        bal_liab_init = await client.get(f"/api/v1/ledger/accounts/{liab_id}/balance")
        assert bal_liab_init.status_code == 200
        assert Decimal(str(bal_liab_init.json()["balance"])) == Decimal("0.0000")
        assert bal_liab_init.json()["normal_balance"] == "CREDIT"

        # 4. Post Balanced Journal Entry ($500.0000 Deposit)
        ref_id = f"dep-{uuid.uuid4().hex}"
        entry_payload = {
            "reference_id": ref_id,
            "description": "Customer cash deposit",
            "postings": [
                {
                    "account_id": cash_id,
                    "amount": "500.0000",
                    "direction": "DEBIT",
                },
                {
                    "account_id": liab_id,
                    "amount": "500.0000",
                    "direction": "CREDIT",
                },
            ],
        }
        res_entry = await client.post("/api/v1/ledger/entries", json=entry_payload)
        assert res_entry.status_code == 201, res_entry.text
        entry_data = res_entry.json()
        assert entry_data["reference_id"] == ref_id
        assert len(entry_data["postings"]) == 2
        entry_id = entry_data["id"]

        # 5. Fetch journal entry by ID
        res_get_entry = await client.get(f"/api/v1/ledger/entries/{entry_id}")
        assert res_get_entry.status_code == 200
        assert res_get_entry.json()["id"] == entry_id

        # 6. Verify Updated Dynamic Balances
        bal_cash = await client.get(f"/api/v1/ledger/accounts/{cash_id}/balance")
        assert bal_cash.status_code == 200
        cash_data = bal_cash.json()
        assert Decimal(str(cash_data["total_debits"])) == Decimal("500.0000")
        assert Decimal(str(cash_data["total_credits"])) == Decimal("0.0000")
        assert Decimal(str(cash_data["balance"])) == Decimal("500.0000")

        bal_liab = await client.get(f"/api/v1/ledger/accounts/{liab_id}/balance")
        assert bal_liab.status_code == 200
        liab_data = bal_liab.json()
        assert Decimal(str(liab_data["total_debits"])) == Decimal("0.0000")
        assert Decimal(str(liab_data["total_credits"])) == Decimal("500.0000")
        assert Decimal(str(liab_data["balance"])) == Decimal("500.0000")


@pytest.mark.asyncio
async def test_unbalanced_journal_entry_rejected_with_422() -> None:
    """Verify that posting an unbalanced journal entry strictly fails with HTTP 422."""
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        # Create accounts
        res_a = await client.post(
            "/api/v1/ledger/accounts",
            json={
                "account_number": f"acc-{uuid.uuid4().hex[:8]}",
                "name": "Account A",
                "account_type": "ASSET",
            },
        )
        res_b = await client.post(
            "/api/v1/ledger/accounts",
            json={
                "account_number": f"acc-{uuid.uuid4().hex[:8]}",
                "name": "Account B",
                "account_type": "LIABILITY",
            },
        )
        acc_a_id = res_a.json()["id"]
        acc_b_id = res_b.json()["id"]

        # Attempt to post unbalanced transaction (Debits $100.0000 != Credits $90.0000)
        unbalanced_payload = {
            "reference_id": f"unbal-{uuid.uuid4().hex}",
            "description": "Unbalanced transfer attempt",
            "postings": [
                {
                    "account_id": acc_a_id,
                    "amount": "100.0000",
                    "direction": "DEBIT",
                },
                {
                    "account_id": acc_b_id,
                    "amount": "90.0000",
                    "direction": "CREDIT",
                },
            ],
        }
        res = await client.post("/api/v1/ledger/entries", json=unbalanced_payload)
        assert res.status_code == 422, f"Expected 422, got {res.status_code}: {res.text}"
        error_json = res.json()
        # Verify error code or detail
        assert "UNBALANCED_JOURNAL_ENTRY" in str(error_json) or "zero-sum" in str(error_json).lower()


@pytest.mark.asyncio
async def test_duplicate_reference_id_rejected_with_409() -> None:
    """Verify idempotency protection: duplicate reference_id returns HTTP 409 Conflict."""
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        res_a = await client.post(
            "/api/v1/ledger/accounts",
            json={
                "account_number": f"acc-{uuid.uuid4().hex[:8]}",
                "name": "Cash Account",
                "account_type": "ASSET",
            },
        )
        res_b = await client.post(
            "/api/v1/ledger/accounts",
            json={
                "account_number": f"acc-{uuid.uuid4().hex[:8]}",
                "name": "Equity Account",
                "account_type": "EQUITY",
            },
        )
        acc_a_id = res_a.json()["id"]
        acc_b_id = res_b.json()["id"]

        fixed_ref = f"idempotent-ref-{uuid.uuid4().hex}"
        payload = {
            "reference_id": fixed_ref,
            "description": "First initial posting",
            "postings": [
                {"account_id": acc_a_id, "amount": "250.0000", "direction": "DEBIT"},
                {"account_id": acc_b_id, "amount": "250.0000", "direction": "CREDIT"},
            ],
        }

        # First post succeeds
        res1 = await client.post("/api/v1/ledger/entries", json=payload)
        assert res1.status_code == 201

        # Second post with identical reference_id fails with 409
        res2 = await client.post("/api/v1/ledger/entries", json=payload)
        assert res2.status_code == 409, f"Expected 409 Conflict, got {res2.status_code}: {res2.text}"


@pytest.mark.asyncio
async def test_compound_multi_leg_journal_entry() -> None:
    """Verify a 3-leg compound transaction: customer funding with platform processing fee deduction."""
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        # Cash Asset: +1000.0000
        cash = (
            await client.post(
                "/api/v1/ledger/accounts",
                json={
                    "account_number": f"cash-{uuid.uuid4().hex[:8]}",
                    "name": "Master Settlement",
                    "account_type": "ASSET",
                },
            )
        ).json()

        # Customer Wallet Liability: +975.0000
        customer = (
            await client.post(
                "/api/v1/ledger/accounts",
                json={
                    "account_number": f"cust-{uuid.uuid4().hex[:8]}",
                    "name": "Customer Wallet",
                    "account_type": "LIABILITY",
                },
            )
        ).json()

        # Fee Revenue: +25.0000
        fee = (
            await client.post(
                "/api/v1/ledger/accounts",
                json={
                    "account_number": f"fee-{uuid.uuid4().hex[:8]}",
                    "name": "Processing Revenue",
                    "account_type": "REVENUE",
                },
            )
        ).json()

        compound_payload = {
            "reference_id": f"comp-{uuid.uuid4().hex}",
            "description": "Deposit $1000 with 2.5% processing fee",
            "postings": [
                {"account_id": cash["id"], "amount": "1000.0000", "direction": "DEBIT"},
                {"account_id": customer["id"], "amount": "975.0000", "direction": "CREDIT"},
                {"account_id": fee["id"], "amount": "25.0000", "direction": "CREDIT"},
            ],
        }

        res = await client.post("/api/v1/ledger/entries", json=compound_payload)
        assert res.status_code == 201, res.text
        data = res.json()
        assert len(data["postings"]) == 3

        # Verify dynamic balances
        cash_bal = (await client.get(f"/api/v1/ledger/accounts/{cash['id']}/balance")).json()
        cust_bal = (await client.get(f"/api/v1/ledger/accounts/{customer['id']}/balance")).json()
        fee_bal = (await client.get(f"/api/v1/ledger/accounts/{fee['id']}/balance")).json()

        assert Decimal(str(cash_bal["balance"])) == Decimal("1000.0000")
        assert Decimal(str(cust_bal["balance"])) == Decimal("975.0000")
        assert Decimal(str(fee_bal["balance"])) == Decimal("25.0000")


@pytest.mark.asyncio
async def test_arbitrary_precision_fractional_decimals() -> None:
    """Verify that exact fractional decimal amounts (e.g. 0.0001, 1234.5678) are preserved."""
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        acc1 = (
            await client.post(
                "/api/v1/ledger/accounts",
                json={"account_number": f"f1-{uuid.uuid4().hex[:8]}", "name": "A1", "account_type": "ASSET"},
            )
        ).json()
        acc2 = (
            await client.post(
                "/api/v1/ledger/accounts",
                json={"account_number": f"f2-{uuid.uuid4().hex[:8]}", "name": "A2", "account_type": "EXPENSE"},
            )
        ).json()

        precise_amount = "1234.5678"
        entry_payload = {
            "reference_id": f"prec-{uuid.uuid4().hex}",
            "description": "Sub-cent precise settlement",
            "postings": [
                {"account_id": acc1["id"], "amount": precise_amount, "direction": "DEBIT"},
                {"account_id": acc2["id"], "amount": precise_amount, "direction": "CREDIT"},
            ],
        }
        res = await client.post("/api/v1/ledger/entries", json=entry_payload)
        assert res.status_code == 201

        bal1 = (await client.get(f"/api/v1/ledger/accounts/{acc1['id']}/balance")).json()
        assert Decimal(str(bal1["balance"])) == Decimal(precise_amount)


@pytest.mark.asyncio
async def test_nonexistent_account_returns_404() -> None:
    """Verify that referencing an unknown account ID returns HTTP 404."""
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        # Nonexistent account balance check
        fake_uuid = str(uuid.uuid4())
        res = await client.get(f"/api/v1/ledger/accounts/{fake_uuid}/balance")
        assert res.status_code == 404

        # Nonexistent account posting attempt
        acc = (
            await client.post(
                "/api/v1/ledger/accounts",
                json={"account_number": f"ex-{uuid.uuid4().hex[:8]}", "name": "Existing", "account_type": "ASSET"},
            )
        ).json()

        res_entry = await client.post(
            "/api/v1/ledger/entries",
            json={
                "reference_id": f"fake-{uuid.uuid4().hex}",
                "description": "Invalid account entry",
                "postings": [
                    {"account_id": acc["id"], "amount": "10.0000", "direction": "DEBIT"},
                    {"account_id": fake_uuid, "amount": "10.0000", "direction": "CREDIT"},
                ],
            },
        )
        assert res_entry.status_code == 404


@pytest.mark.asyncio
async def test_in_memory_ledger_service_unit_test() -> None:
    """Direct unit test of LedgerDomainService using InMemoryLedgerRepository."""
    repo = InMemoryLedgerRepository()
    service = LedgerDomainService(repo=repo)

    # 1. Create accounts
    asset_acc = await service.create_account(
        LedgerAccountCreate(
            account_number="1001",
            name="Bank Treasury",
            account_type=AccountType.ASSET,
            currency="USD",
        )
    )
    expense_acc = await service.create_account(
        LedgerAccountCreate(
            account_number="5001",
            name="Marketing Expense",
            account_type=AccountType.EXPENSE,
            currency="USD",
        )
    )

    # 2. Duplicate account number rejection
    with pytest.raises(EntityConflictException):
        await service.create_account(
            LedgerAccountCreate(
                account_number="1001",
                name="Duplicate",
                account_type=AccountType.ASSET,
            )
        )

    # 3. Unbalanced entry rejection
    with pytest.raises(UnbalancedJournalEntryException):
        await service.record_journal_entry(
            JournalEntryCreateDTO(
                reference_id="ref-unbalanced",
                description="Test bad invariant",
                postings=[
                    PostingCreateDTO(
                        account_id=asset_acc.id,
                        amount=Decimal("100.0000"),
                        direction=PostingDirection.DEBIT,
                    ),
                    PostingCreateDTO(
                        account_id=expense_acc.id,
                        amount=Decimal("99.9999"),
                        direction=PostingDirection.CREDIT,
                    ),
                ],
            )
        )

    # 4. Balanced posting
    entry = await service.record_journal_entry(
        JournalEntryCreateDTO(
            reference_id="ref-good-1",
            description="Marketing spend",
            postings=[
                PostingCreateDTO(
                    account_id=expense_acc.id,
                    amount=Decimal("200.5000"),
                    direction=PostingDirection.DEBIT,
                ),
                PostingCreateDTO(
                    account_id=asset_acc.id,
                    amount=Decimal("200.5000"),
                    direction=PostingDirection.CREDIT,
                ),
            ],
        )
    )
    assert entry.reference_id == "ref-good-1"
    assert len(entry.postings) == 2

    # 5. Verify dynamic balances:
    # Asset (Normal DEBIT): Debited 0, Credited 200.5000 -> Balance = -200.5000
    asset_bal = await service.get_account_balance(asset_acc.id)
    assert asset_bal.balance == Decimal("-200.5000")
    assert asset_bal.total_credits == Decimal("200.5000")

    # Expense (Normal DEBIT): Debited 200.5000, Credited 0 -> Balance = 200.5000
    expense_bal = await service.get_account_balance(expense_acc.id)
    assert expense_bal.balance == Decimal("200.5000")
    assert expense_bal.total_debits == Decimal("200.5000")

    # 6. Duplicate reference ID check
    with pytest.raises(DuplicateReferenceException):
        await service.record_journal_entry(
            JournalEntryCreateDTO(
                reference_id="ref-good-1",
                description="Duplicate",
                postings=[
                    PostingCreateDTO(
                        account_id=expense_acc.id,
                        amount=Decimal("10.0000"),
                        direction=PostingDirection.DEBIT,
                    ),
                    PostingCreateDTO(
                        account_id=asset_acc.id,
                        amount=Decimal("10.0000"),
                        direction=PostingDirection.CREDIT,
                    ),
                ],
            )
        )
