"""Test Suite for Day 41: Distributed Locking Architecture (Redlock Pattern & Atomic Lua Mutex).

Verifies:
1. Mutual Exclusion: Worker A holds lock; Worker B fails to acquire immediately.
2. Safe Token-Checked Release:
   - Releasing with valid token removes Redis key.
   - Releasing with invalid token returns False and preserves lock key.
3. Deadlock Recovery via TTL Expiration:
   - Abandoned lock auto-expires after TTL; second worker acquires cleanly.
4. Concurrent Multi-Node Race Test (Mathematical Invariant):
   - 10 concurrent workers compete for identical lock; EXACTLY 1 succeeds, 9 fail.
5. Async Context Manager Lifecycle:
   - Safe acquire on __aenter__ and guaranteed release on __aexit__.
6. HTTP Transport & API Integration:
   - POST /jobs/execute-exclusive/{job_name} executes under lock and returns HTTP 200.
   - Concurrent requests receive HTTP 409 Conflict with code 'LOCK_CONFLICT'.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.dsa.distributed_lock import DistributedLock
from app.core.exceptions import DistributedLockConflictException
from app.main import app
from app.services.job_service import JobService

# =============================================================================
# 1. Pure DSA DistributedLock Unit Tests
# =============================================================================


@pytest.mark.asyncio
async def test_mutual_exclusion_primitive(fake_redis: Any) -> None:
    """Worker A acquires lock; Worker B attempting the same resource immediately fails."""
    lock_a = DistributedLock(redis=fake_redis, name="resource_alpha", ttl_ms=5000)
    lock_b = DistributedLock(redis=fake_redis, name="resource_alpha", ttl_ms=5000)

    acquired_a = await lock_a.acquire()
    assert acquired_a is True
    assert lock_a.is_acquired is True
    assert lock_a.token is not None

    # Worker B must fail immediately
    acquired_b = await lock_b.acquire()
    assert acquired_b is False
    assert lock_b.is_acquired is False
    assert lock_b.token is None

    # After Worker A releases, Worker B can acquire
    released_a = await lock_a.release()
    assert released_a is True
    assert lock_a.is_acquired is False

    acquired_b_retry = await lock_b.acquire()
    assert acquired_b_retry is True
    assert lock_b.is_acquired is True
    await lock_b.release()


@pytest.mark.asyncio
async def test_safe_token_checked_release_via_lua(fake_redis: Any) -> None:
    """Safe release via Lua script: matching token deletes key; foreign token preserves key."""
    lock = DistributedLock(redis=fake_redis, name="safe_release_test", ttl_ms=5000)
    await lock.acquire()
    valid_token = lock.token
    assert valid_token is not None

    # Attempt release with forged/tampered token
    lock.token = "forged_malicious_token_123"
    # Execute Lua release directly with forged token
    from app.core.dsa.distributed_lock import _RELEASE_LUA_SCRIPT

    tampered_result = await fake_redis.eval(_RELEASE_LUA_SCRIPT, 1, lock.key, "forged_malicious_token_123")
    assert int(tampered_result) == 0, "Forged token must not release the lock"

    # Lock key must still exist in Redis with original owner's token
    current_stored_token = await fake_redis.get(lock.key)
    assert current_stored_token == valid_token

    # Restore correct token and release safely
    lock.token = valid_token
    released = await lock.release()
    assert released is True
    assert await fake_redis.get(lock.key) is None


@pytest.mark.asyncio
async def test_deadlock_recovery_via_ttl_expiration(fake_redis: Any) -> None:
    """Deadlock recovery: Worker A crashes/abandons lock with 100ms TTL; Worker B acquires cleanly after expiration."""
    lock_a = DistributedLock(redis=fake_redis, name="abandoned_task", ttl_ms=100)
    lock_b = DistributedLock(redis=fake_redis, name="abandoned_task", ttl_ms=5000)

    # Worker A acquires and abandons (no release called)
    assert await lock_a.acquire() is True

    # Immediate attempt by Worker B fails
    assert await lock_b.acquire() is False

    # Wait for TTL to expire
    await asyncio.sleep(0.15)

    # Worker B can now acquire cleanly, proving zero permanent deadlocks
    assert await lock_b.acquire() is True
    await lock_b.release()


@pytest.mark.asyncio
async def test_async_context_manager_lifecycle(fake_redis: Any) -> None:
    """Async context manager (__aenter__ / __aexit__) safely acquires and guarantees release."""
    lock = DistributedLock(redis=fake_redis, name="ctx_resource", ttl_ms=5000)

    async with lock as acquired:
        assert acquired is True
        assert await fake_redis.get(lock.key) is not None

    # After exit, key is unconditionally released
    assert await fake_redis.get(lock.key) is None
    assert lock.is_acquired is False


# =============================================================================
# 2. Concurrency Race & Mathematical Invariant Tests
# =============================================================================


@pytest.mark.asyncio
async def test_concurrent_multi_node_race_exact_one_winner(fake_redis: Any) -> None:
    """Mathematical Invariant: 10 concurrent async workers race for the same lock.

    Guarantees:
    - EXACTLY 1 worker successfully acquires the lock.
    - EXACTLY 9 workers fail to acquire.
    """
    concurrency_count = 10
    workers = [
        DistributedLock(redis=fake_redis, name="exclusive_gold_ticket", ttl_ms=5000) for _ in range(concurrency_count)
    ]

    # Fire all 10 acquire attempts simultaneously
    results = await asyncio.gather(*(w.acquire() for w in workers))

    success_count = results.count(True)
    failure_count = results.count(False)

    assert success_count == 1, f"Expected EXACTLY 1 winner, got {success_count}. Results: {results}"
    assert failure_count == 9, f"Expected EXACTLY 9 losers, got {failure_count}. Results: {results}"

    # Winner releases lock
    winner = next(w for w in workers if w.is_acquired)
    assert await winner.release() is True


# =============================================================================
# 3. Service Layer & HTTP Integration Tests
# =============================================================================


@pytest.mark.asyncio
async def test_job_service_execute_exclusive_job(fake_redis: Any) -> None:
    """JobService.execute_exclusive_job runs job under lock and raises DistributedLockConflictException on collision."""
    service = JobService(redis_client=fake_redis)

    # 1. Successful execution
    result = await service.execute_exclusive_job(
        job_name="sync_analytics",
        payload={"batch_size": 100},
        ttl_ms=5000,
    )
    assert result["job_name"] == "sync_analytics"
    assert result["status"] == "completed"
    assert result["payload"] == {"batch_size": 100}
    assert "execution_token" in result

    # 2. Lock collision test: artificially hold lock
    holder = DistributedLock(redis=fake_redis, name="sync_analytics", ttl_ms=5000)
    await holder.acquire()

    with pytest.raises(DistributedLockConflictException) as exc_info:
        await service.execute_exclusive_job(
            job_name="sync_analytics",
            payload={"batch_size": 50},
        )
    assert exc_info.value.code == "LOCK_CONFLICT"

    await holder.release()


@pytest.mark.asyncio
async def test_api_exclusive_job_endpoint(fake_redis: Any) -> None:
    """HTTP API endpoint POST /jobs/execute-exclusive/{job_name} execution and conflict rejection."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Normal successful execution -> HTTP 200
        res = await client.post(
            "/jobs/execute-exclusive/nightly_billing",
            json={"payload": {"currency": "USD"}, "ttl_ms": 5000},
        )
        assert res.status_code == 200
        data = res.json()
        assert data["job_name"] == "nightly_billing"
        assert data["status"] == "completed"
        assert data["payload"] == {"currency": "USD"}
        assert len(data["execution_token"]) > 0

        # 2. Artificially lock the resource and assert HTTP 409 Conflict
        blocker = DistributedLock(redis=fake_redis, name="nightly_billing", ttl_ms=5000)
        await blocker.acquire()

        conflict_res = await client.post(
            "/jobs/execute-exclusive/nightly_billing",
            json={"payload": {"currency": "EUR"}},
        )
        assert conflict_res.status_code == 409
        err = conflict_res.json()
        assert err["error"]["code"] == "LOCK_CONFLICT"
        assert "Resource is currently locked by another concurrent process" in err["error"]["message"]

        await blocker.release()
