"""Comprehensive Test Suite for Day 84: Fintech Atomic Money Transfer Architecture.

Validates:
1. Multi-Leg Atomic P2P Transfers via Unit of Work (ACID Isolation).
2. Multi-Leg Transfers with Platform Fee Deductions (Conservation of Money).
3. Strict Pre-Transfer Non-Negative Balance Guard (HTTP 422 Insufficient Funds).
4. Zero-Sum Balance Invariant across all transfer legs.
5. Self-Transfer Prohibition (HTTP 400).
6. Idempotency & Replay Protection via Reference ID (HTTP 409 Conflict).
7. In-Memory Unit of Work Fast Unit Tests with Snapshot Rollback Verification.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import httpx
import pytest

from app.core.exceptions import (
    DuplicateReferenceException,
    InsufficientFundsException,
    SelfTransferNotAllowedException,
)
from app.core.unit_of_work import InMemoryUnitOfWork
from app.main import app
from app.schemas.ledger import AccountType, PostingCreateDTO, PostingDirection
from app.services.ledger_transfer_service import LedgerTransferService


@pytest.mark.asyncio
async def test_successful_p2p_fund_transfer() -> None:
    """Verify an atomic P2P transfer between two accounts with real-time balance updates."""
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        # 1. Create Alice (Source) and Bob (Destination) accounts
        alice_num = f"alice-{uuid.uuid4().hex[:8]}"
        bob_num = f"bob-{uuid.uuid4().hex[:8]}"
        reserve_num = f"reserve-{uuid.uuid4().hex[:8]}"

        res_alice = await client.post(
            "/api/v1/ledger/accounts",
            json={"account_number": alice_num, "name": "Alice Wallet", "account_type": "ASSET"},
        )
        assert res_alice.status_code == 201
        alice_id = res_alice.json()["id"]

        res_bob = await client.post(
            "/api/v1/ledger/accounts",
            json={"account_number": bob_num, "name": "Bob Wallet", "account_type": "ASSET"},
        )
        assert res_bob.status_code == 201
        bob_id = res_bob.json()["id"]

        res_res = await client.post(
            "/api/v1/ledger/accounts",
            json={"account_number": reserve_num, "name": "Cash Vault", "account_type": "LIABILITY"},
        )
        assert res_res.status_code == 201
        reserve_id = res_res.json()["id"]

        # 2. Fund Alice's account with $1000.0000 via a balanced deposit
        fund_ref = f"fund-{uuid.uuid4().hex}"
        await client.post(
            "/api/v1/ledger/entries",
            json={
                "reference_id": fund_ref,
                "description": "Initial funding for Alice",
                "postings": [
                    {"account_id": alice_id, "amount": "1000.0000", "direction": "DEBIT"},
                    {"account_id": reserve_id, "amount": "1000.0000", "direction": "CREDIT"},
                ],
            },
        )

        # Check Alice's balance before transfer
        bal_alice_pre = (await client.get(f"/api/v1/ledger/accounts/{alice_id}/balance")).json()
        assert Decimal(str(bal_alice_pre["balance"])) == Decimal("1000.0000")

        # 3. Transfer $400.0000 from Alice to Bob
        tx_ref = f"tx-p2p-{uuid.uuid4().hex}"
        transfer_payload = {
            "source_account_id": alice_id,
            "destination_account_id": bob_id,
            "amount": "400.0000",
            "reference_id": tx_ref,
            "description": "Dinner bill split reimbursement",
        }

        res_transfer = await client.post("/api/v1/ledger/transfers", json=transfer_payload)
        assert res_transfer.status_code == 201, res_transfer.text
        transfer_data = res_transfer.json()

        assert transfer_data["reference_id"] == tx_ref
        assert Decimal(str(transfer_data["transferred_amount"])) == Decimal("400.0000")
        assert Decimal(str(transfer_data["fee_deducted"])) == Decimal("0.0000")
        assert Decimal(str(transfer_data["source_new_balance"])) == Decimal("600.0000")
        assert Decimal(str(transfer_data["destination_new_balance"])) == Decimal("400.0000")

        # 4. Verify persisted dynamic balances via GET /accounts/{id}/balance
        bal_alice_post = (await client.get(f"/api/v1/ledger/accounts/{alice_id}/balance")).json()
        bal_bob_post = (await client.get(f"/api/v1/ledger/accounts/{bob_id}/balance")).json()

        assert Decimal(str(bal_alice_post["balance"])) == Decimal("600.0000")
        assert Decimal(str(bal_bob_post["balance"])) == Decimal("400.0000")


@pytest.mark.asyncio
async def test_multi_leg_transfer_with_platform_fee() -> None:
    """Verify 3-leg transfer with platform processing fee deduction preserving zero-sum balance."""
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        # Create Source, Destination, and Platform Fee accounts
        src = (
            await client.post(
                "/api/v1/ledger/accounts",
                json={"account_number": f"src-{uuid.uuid4().hex[:8]}", "name": "Buyer Cash", "account_type": "ASSET"},
            )
        ).json()
        dst = (
            await client.post(
                "/api/v1/ledger/accounts",
                json={"account_number": f"dst-{uuid.uuid4().hex[:8]}", "name": "Seller Cash", "account_type": "ASSET"},
            )
        ).json()
        fee = (
            await client.post(
                "/api/v1/ledger/accounts",
                json={
                    "account_number": f"fee-{uuid.uuid4().hex[:8]}",
                    "name": "Platform Fee Account",
                    "account_type": "ASSET",
                },
            )
        ).json()
        vault = (
            await client.post(
                "/api/v1/ledger/accounts",
                json={"account_number": f"vlt-{uuid.uuid4().hex[:8]}", "name": "Vault", "account_type": "LIABILITY"},
            )
        ).json()

        # Fund Source with $2000.0000
        await client.post(
            "/api/v1/ledger/entries",
            json={
                "reference_id": f"fund-{uuid.uuid4().hex}",
                "description": "Funding buyer",
                "postings": [
                    {"account_id": src["id"], "amount": "2000.0000", "direction": "DEBIT"},
                    {"account_id": vault["id"], "amount": "2000.0000", "direction": "CREDIT"},
                ],
            },
        )

        # Execute transfer: $1000.0000 to seller + $20.0000 platform fee
        tx_ref = f"fee-tx-{uuid.uuid4().hex}"
        res = await client.post(
            "/api/v1/ledger/transfers",
            json={
                "source_account_id": src["id"],
                "destination_account_id": dst["id"],
                "amount": "1000.0000",
                "fee_amount": "20.0000",
                "fee_account_id": fee["id"],
                "reference_id": tx_ref,
                "description": "Marketplace purchase with 2% fee",
            },
        )
        assert res.status_code == 201, res.text
        data = res.json()

        assert Decimal(str(data["transferred_amount"])) == Decimal("1000.0000")
        assert Decimal(str(data["fee_deducted"])) == Decimal("20.0000")
        assert Decimal(str(data["source_new_balance"])) == Decimal("980.0000")
        assert Decimal(str(data["destination_new_balance"])) == Decimal("1000.0000")

        # Verify all 3 account balances
        src_bal = (await client.get(f"/api/v1/ledger/accounts/{src['id']}/balance")).json()
        dst_bal = (await client.get(f"/api/v1/ledger/accounts/{dst['id']}/balance")).json()
        fee_bal = (await client.get(f"/api/v1/ledger/accounts/{fee['id']}/balance")).json()

        assert Decimal(str(src_bal["balance"])) == Decimal("980.0000")
        assert Decimal(str(dst_bal["balance"])) == Decimal("1000.0000")
        assert Decimal(str(fee_bal["balance"])) == Decimal("20.0000")

        # Verify underlying journal entry has 3 legs and total debits == total credits == 1020.0000
        journal_entry = (await client.get(f"/api/v1/ledger/entries/{data['journal_entry_id']}")).json()
        postings = journal_entry["postings"]
        assert len(postings) == 3

        debits = sum((Decimal(str(p["amount"])) for p in postings if p["direction"] == "DEBIT"), Decimal("0.0000"))
        credits = sum((Decimal(str(p["amount"])) for p in postings if p["direction"] == "CREDIT"), Decimal("0.0000"))
        assert debits == Decimal("1020.0000")
        assert credits == Decimal("1020.0000")


@pytest.mark.asyncio
async def test_insufficient_funds_rejection_and_atomic_rollback() -> None:
    """Verify that transfers exceeding cleared balance are rejected with HTTP 422 and 0 state changed."""
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        src = (
            await client.post(
                "/api/v1/ledger/accounts",
                json={
                    "account_number": f"broke-{uuid.uuid4().hex[:8]}",
                    "name": "Low Balance",
                    "account_type": "ASSET",
                },
            )
        ).json()
        dst = (
            await client.post(
                "/api/v1/ledger/accounts",
                json={"account_number": f"rcv-{uuid.uuid4().hex[:8]}", "name": "Receiver", "account_type": "ASSET"},
            )
        ).json()
        vault = (
            await client.post(
                "/api/v1/ledger/accounts",
                json={"account_number": f"vlt-{uuid.uuid4().hex[:8]}", "name": "Vault", "account_type": "LIABILITY"},
            )
        ).json()

        # Fund Source with only $100.0000
        await client.post(
            "/api/v1/ledger/entries",
            json={
                "reference_id": f"fund-low-{uuid.uuid4().hex}",
                "description": "Funding $100",
                "postings": [
                    {"account_id": src["id"], "amount": "100.0000", "direction": "DEBIT"},
                    {"account_id": vault["id"], "amount": "100.0000", "direction": "CREDIT"},
                ],
            },
        )

        # Attempt to transfer $150.0000 (exceeds balance)
        res = await client.post(
            "/api/v1/ledger/transfers",
            json={
                "source_account_id": src["id"],
                "destination_account_id": dst["id"],
                "amount": "150.0000",
                "reference_id": f"fail-tx-{uuid.uuid4().hex}",
                "description": "Overdraft attempt",
            },
        )
        assert res.status_code == 422, f"Expected 422, got {res.status_code}: {res.text}"
        error_body = res.json()
        assert "INSUFFICIENT_FUNDS" in str(error_body) or "insufficient funds" in str(error_body).lower()

        # Verify atomic rollback: Source balance remains strictly $100.0000, Destination is $0.0000
        src_bal = (await client.get(f"/api/v1/ledger/accounts/{src['id']}/balance")).json()
        dst_bal = (await client.get(f"/api/v1/ledger/accounts/{dst['id']}/balance")).json()

        assert Decimal(str(src_bal["balance"])) == Decimal("100.0000")
        assert Decimal(str(dst_bal["balance"])) == Decimal("0.0000")


@pytest.mark.asyncio
async def test_self_transfer_prohibited() -> None:
    """Verify that initiating a transfer where source == destination is rejected with HTTP 400."""
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        acc = (
            await client.post(
                "/api/v1/ledger/accounts",
                json={"account_number": f"self-{uuid.uuid4().hex[:8]}", "name": "Self Wallet", "account_type": "ASSET"},
            )
        ).json()

        res = await client.post(
            "/api/v1/ledger/transfers",
            json={
                "source_account_id": acc["id"],
                "destination_account_id": acc["id"],
                "amount": "50.0000",
                "reference_id": f"self-tx-{uuid.uuid4().hex}",
                "description": "Self transfer attempt",
            },
        )
        assert res.status_code == 400, f"Expected 400, got {res.status_code}: {res.text}"
        error_body = res.json()
        assert "SELF_TRANSFER_PROHIBITED" in str(error_body) or "self-transfer" in str(error_body).lower()


@pytest.mark.asyncio
async def test_duplicate_reference_id_replay_conflict() -> None:
    """Verify idempotency protection: Replaying the same transfer reference ID returns HTTP 409."""
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        src = (
            await client.post(
                "/api/v1/ledger/accounts",
                json={"account_number": f"idemp-src-{uuid.uuid4().hex[:8]}", "name": "S1", "account_type": "ASSET"},
            )
        ).json()
        dst = (
            await client.post(
                "/api/v1/ledger/accounts",
                json={"account_number": f"idemp-dst-{uuid.uuid4().hex[:8]}", "name": "D1", "account_type": "ASSET"},
            )
        ).json()
        vault = (
            await client.post(
                "/api/v1/ledger/accounts",
                json={"account_number": f"vlt-{uuid.uuid4().hex[:8]}", "name": "V", "account_type": "LIABILITY"},
            )
        ).json()

        # Fund $500
        await client.post(
            "/api/v1/ledger/entries",
            json={
                "reference_id": f"fund-idemp-{uuid.uuid4().hex}",
                "description": "Fund",
                "postings": [
                    {"account_id": src["id"], "amount": "500.0000", "direction": "DEBIT"},
                    {"account_id": vault["id"], "amount": "500.0000", "direction": "CREDIT"},
                ],
            },
        )

        fixed_ref = f"unique-transfer-{uuid.uuid4().hex}"
        payload = {
            "source_account_id": src["id"],
            "destination_account_id": dst["id"],
            "amount": "100.0000",
            "reference_id": fixed_ref,
            "description": "First attempt",
        }

        # First request succeeds
        res1 = await client.post("/api/v1/ledger/transfers", json=payload)
        assert res1.status_code == 201

        # Second request with identical reference ID returns 409
        res2 = await client.post("/api/v1/ledger/transfers", json=payload)
        assert res2.status_code == 409, f"Expected 409 Conflict, got {res2.status_code}: {res2.text}"


@pytest.mark.asyncio
async def test_inactive_account_transfer_rejection() -> None:
    """Verify that attempting to transfer from or to a nonexistent account returns HTTP 404."""
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        acc = (
            await client.post(
                "/api/v1/ledger/accounts",
                json={"account_number": f"real-{uuid.uuid4().hex[:8]}", "name": "Real", "account_type": "ASSET"},
            )
        ).json()
        fake_id = str(uuid.uuid4())

        # From real to fake
        res1 = await client.post(
            "/api/v1/ledger/transfers",
            json={
                "source_account_id": acc["id"],
                "destination_account_id": fake_id,
                "amount": "50.0000",
                "reference_id": f"fake-dst-{uuid.uuid4().hex}",
                "description": "Transfer to ghost",
            },
        )
        assert res1.status_code == 404

        # From fake to real
        res2 = await client.post(
            "/api/v1/ledger/transfers",
            json={
                "source_account_id": fake_id,
                "destination_account_id": acc["id"],
                "amount": "50.0000",
                "reference_id": f"fake-src-{uuid.uuid4().hex}",
                "description": "Transfer from ghost",
            },
        )
        assert res2.status_code == 404


@pytest.mark.asyncio
async def test_in_memory_uow_atomic_transfer_unit_test() -> None:
    """Direct unit test of LedgerTransferService with InMemoryUnitOfWork validating snapshot rollback."""
    uow = InMemoryUnitOfWork()
    service = LedgerTransferService(uow=uow)

    # 1. Create accounts
    async with uow:
        acc_a = await uow.ledger.create_account(
            account_number="A-100", name="Account A", account_type=AccountType.ASSET
        )
        acc_b = await uow.ledger.create_account(
            account_number="B-200", name="Account B", account_type=AccountType.ASSET
        )
        vault = await uow.ledger.create_account(
            account_number="V-999", name="Vault", account_type=AccountType.LIABILITY
        )
        # Fund Account A with $300.0000
        await uow.ledger.create_journal_entry(
            reference_id="init-funding",
            description="Init",
            postings=[
                PostingCreateDTO(account_id=acc_a.id, amount=Decimal("300.0000"), direction=PostingDirection.DEBIT),
                PostingCreateDTO(account_id=vault.id, amount=Decimal("300.0000"), direction=PostingDirection.CREDIT),
            ],
        )
        await uow.commit()

    # 2. Test self-transfer guard
    with pytest.raises(SelfTransferNotAllowedException):
        await service.transfer_funds(
            source_account_id=acc_a.id,
            destination_account_id=acc_a.id,
            amount=Decimal("50.0000"),
            reference_id="self-fail",
            description="Self transfer",
        )

    # 3. Test insufficient funds rollback
    with pytest.raises(InsufficientFundsException):
        await service.transfer_funds(
            source_account_id=acc_a.id,
            destination_account_id=acc_b.id,
            amount=Decimal("350.0000"),  # > 300
            reference_id="overdraft-fail",
            description="Overdraft",
        )

    # Check Account A balance is still exactly $300.0000
    debits, credits = await uow.ledger.get_account_balance_aggregates(acc_a.id)
    assert debits - credits == Decimal("300.0000")

    # 4. Successful transfer $120.0000
    resp = await service.transfer_funds(
        source_account_id=acc_a.id,
        destination_account_id=acc_b.id,
        amount=Decimal("120.0000"),
        reference_id="tx-inmem-ok",
        description="Transfer ok",
    )
    assert resp.source_new_balance == Decimal("180.0000")
    assert resp.destination_new_balance == Decimal("120.0000")

    # 5. Duplicate reference replay
    with pytest.raises(DuplicateReferenceException):
        await service.transfer_funds(
            source_account_id=acc_a.id,
            destination_account_id=acc_b.id,
            amount=Decimal("10.0000"),
            reference_id="tx-inmem-ok",
            description="Duplicate",
        )
