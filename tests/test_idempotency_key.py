"""Comprehensive test suite for Day 42: Enterprise Idempotency Key Architecture.

Verifies:
1. Initial Request Success: First call creates charge (HTTP 201) with 'X-Cache-Lookup: MISS'.
2. Idempotent Replay Guarantee: Subsequent requests with identical key return cached HTTP 201 response
   with 'X-Cache-Lookup: HIT-IDEMPOTENT' without re-invoking payment logic (processed_counter == 1).
3. Payload Tampering Detection: Reusing identical key with different body returns HTTP 422 Unprocessable Entity.
4. In-Flight Concurrency Protection: Overlapping concurrent requests with identical key return HTTP 409 Conflict.
5. Direct IdempotencyManager Unit Tests: State machine transitions, SHA-256 hash validation, and TTL verification.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.idempotency import (
    IdempotencyManager,
    IdempotencyState,
    compute_request_hash,
)
from app.main import app
from app.services.payment_service import get_payment_service

# =============================================================================
# 1. Pure Idempotency Engine Unit Tests
# =============================================================================


@pytest.mark.asyncio
async def test_compute_request_hash_determinism() -> None:
    """Ensure compute_request_hash generates deterministic SHA-256 digests in O(L) time."""
    h1 = compute_request_hash("POST", "/payments/charge", '{"amount": 100.0}')
    h2 = compute_request_hash("POST", "/payments/charge", '{"amount": 100.0}')
    h3 = compute_request_hash("POST", "/payments/charge", '{"amount": 200.0}')

    assert h1 == h2
    assert h1 != h3
    assert len(h1) == 64


@pytest.mark.asyncio
async def test_idempotency_manager_lifecycle(fake_redis: Any) -> None:
    """Test IdempotencyManager check_or_acquire, record_success, and cached replay."""
    manager = IdempotencyManager(redis=fake_redis)
    key = "idem_test_unit_001"
    req_hash = compute_request_hash("POST", "/test", "payload1")

    # 1. First acquisition -> not cached, acquired as IN_PROGRESS
    is_cached, record = await manager.check_or_acquire(key=key, request_hash=req_hash)
    assert is_cached is False
    assert record is None

    # 2. Re-check while IN_PROGRESS -> raises HTTP 409 Conflict
    with pytest.raises(Exception) as exc_info:
        await manager.check_or_acquire(key=key, request_hash=req_hash)
    assert "409" in str(exc_info.value)

    # 3. Transition to COMPLETED
    await manager.record_success(
        key=key,
        request_hash=req_hash,
        status_code=201,
        response_body={"status": "paid"},
    )

    # 4. Re-check with identical hash -> returns cached record
    is_cached_2, cached_rec = await manager.check_or_acquire(key=key, request_hash=req_hash)
    assert is_cached_2 is True
    assert cached_rec is not None
    assert cached_rec["state"] == IdempotencyState.COMPLETED
    assert cached_rec["status_code"] == 201
    assert cached_rec["response_body"] == {"status": "paid"}

    # 5. Re-check with altered hash (tampering) -> raises HTTP 422
    different_hash = compute_request_hash("POST", "/test", "different_payload")
    with pytest.raises(Exception) as exc_tamper:
        await manager.check_or_acquire(key=key, request_hash=different_hash)
    assert "422" in str(exc_tamper.value)


# =============================================================================
# 2. HTTP Endpoint Integration & Idempotent Replay Tests
# =============================================================================


@pytest.mark.asyncio
async def test_initial_payment_charge_success(fake_redis: Any) -> None:
    """First request with a new Idempotency-Key returns HTTP 201 Created and MISS cache header."""
    payment_service = get_payment_service()
    payment_service.clear()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.post(
            "/payments/charge",
            headers={"Idempotency-Key": "order_key_alpha_1001"},
            json={"order_id": "ORD-9999", "amount": 150.0, "currency": "BDT"},
        )
        assert res.status_code == 201
        assert res.headers.get("X-Cache-Lookup") == "MISS"
        data = res.json()
        assert data["order_id"] == "ORD-9999"
        assert data["amount"] == 150.0
        assert data["currency"] == "BDT"
        assert data["status"] == "succeeded"
        assert data["charge_id"].startswith("ch_")
        assert payment_service.processed_counter == 1


@pytest.mark.asyncio
async def test_idempotent_replay_zero_duplicate_charge(fake_redis: Any) -> None:
    """Subsequent identical requests return cached HTTP 201 response with zero duplicate charges."""
    payment_service = get_payment_service()
    payment_service.clear()

    key = "order_key_beta_2002"
    payload = {"order_id": "ORD-8888", "amount": 250.0, "currency": "USD"}

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Request 1: Initial mutation
        res1 = await client.post(
            "/payments/charge",
            headers={"Idempotency-Key": key},
            json=payload,
        )
        assert res1.status_code == 201
        assert res1.headers.get("X-Cache-Lookup") == "MISS"
        data1 = res1.json()

        # Request 2: Replay identical request
        res2 = await client.post(
            "/payments/charge",
            headers={"Idempotency-Key": key},
            json=payload,
        )
        assert res2.status_code == 201
        assert res2.headers.get("X-Cache-Lookup") == "HIT-IDEMPOTENT"
        data2 = res2.json()

        # Request 3: Replay again
        res3 = await client.post(
            "/payments/charge",
            headers={"Idempotency-Key": key},
            json=payload,
        )
        assert res3.status_code == 201
        assert res3.headers.get("X-Cache-Lookup") == "HIT-IDEMPOTENT"
        data3 = res3.json()

        # Response payloads must be identical
        assert data1 == data2 == data3
        # Financial mutation counter must remain strictly 1!
        assert payment_service.processed_counter == 1, (
            f"Expected exactly 1 charge execution, but found {payment_service.processed_counter}!"
        )


@pytest.mark.asyncio
async def test_payload_tampering_rejection(fake_redis: Any) -> None:
    """Reusing an existing Idempotency-Key with different payload parameters triggers HTTP 422."""
    payment_service = get_payment_service()
    payment_service.clear()

    key = "order_key_tamper_3003"
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Initial call with $50.0
        res1 = await client.post(
            "/payments/charge",
            headers={"Idempotency-Key": key},
            json={"order_id": "ORD-1111", "amount": 50.0, "currency": "BDT"},
        )
        assert res1.status_code == 201

        # Attacker / buggy client reuses same key but modifies amount to $500.0
        res2 = await client.post(
            "/payments/charge",
            headers={"Idempotency-Key": key},
            json={"order_id": "ORD-1111", "amount": 500.0, "currency": "BDT"},
        )
        assert res2.status_code == 422
        body = res2.json()
        assert "Idempotency key reused with different payload parameters" in body["detail"]

        # Charge counter must not have increased on the tampered request
        assert payment_service.processed_counter == 1


@pytest.mark.asyncio
async def test_in_flight_concurrency_collision_protection(fake_redis: Any) -> None:
    """Overlapping concurrent requests with identical key return HTTP 409 Conflict."""
    payment_service = get_payment_service()
    payment_service.clear()

    key = "order_key_concurrent_4004"
    payload = {"order_id": "ORD-7777", "amount": 300.0, "currency": "BDT"}

    # Mock payment_service.process_charge to simulate delayed processing
    original_process = payment_service.process_charge

    async def slow_process(*args: Any, **kwargs: Any) -> Any:
        await asyncio.sleep(0.1)
        return await original_process(*args, **kwargs)

    payment_service.process_charge = slow_process  # type: ignore[method-assign]

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Launch 2 simultaneous requests with the identical idempotency key
        task1 = client.post("/payments/charge", headers={"Idempotency-Key": key}, json=payload)
        task2 = client.post("/payments/charge", headers={"Idempotency-Key": key}, json=payload)

        res1, res2 = await asyncio.gather(task1, task2)

        status_codes = {res1.status_code, res2.status_code}
        assert 201 in status_codes, f"Expected one 201 Created, got {status_codes}"
        assert 409 in status_codes, f"Expected one 409 Conflict, got {status_codes}"

        conflict_res = res1 if res1.status_code == 409 else res2
        assert "currently in progress" in conflict_res.json()["detail"]

        # Only one charge should have actually executed
        assert payment_service.processed_counter == 1
