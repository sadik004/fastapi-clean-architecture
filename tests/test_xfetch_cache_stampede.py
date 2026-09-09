"""Integration and unit tests for Day 34: Cache Stampede Prevention via XFetch Algorithm."""

from __future__ import annotations

import asyncio
import math
import time
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.dsa.xfetch import XFetchEnvelope, should_recompute
from app.main import app
from app.services.cache_service import CacheService
from app.services.user_service import XFETCH_USER_PREFIX

# =============================================================================
# 1. Pure Algorithm Unit Tests
# =============================================================================


def test_xfetch_pure_math_boundary_conditions() -> None:
    """Verify XFetch probability boundary invariants."""
    # When current_time >= expiry, should always return True (hard expired)
    assert should_recompute(delta=0.05, expiry=100.0, now=100.0) is True
    assert should_recompute(delta=0.05, expiry=100.0, now=105.0) is True

    # When far from expiry (remaining TTL is large, e.g. 300s) and delta is small (e.g. 0.05s)
    # (- 1.0 * 0.05 * ln(r)) will be at most ~ 0.5 for realistic r >= 0.0001
    # remaining_ttl = 300 > 0.5, so should_recompute MUST be False
    assert should_recompute(delta=0.05, expiry=400.0, now=100.0, beta=1.0, rand_val=0.5) is False
    assert should_recompute(delta=0.05, expiry=400.0, now=100.0, beta=1.0, rand_val=0.001) is False

    # When very close to expiry (remaining TTL is 0.01s) and delta is 0.05s
    # threshold = - 1.0 * 0.05 * ln(0.5) = 0.05 * 0.693 = 0.03465 > 0.01
    # should_recompute MUST be True
    assert should_recompute(delta=0.05, expiry=100.01, now=100.0, beta=1.0, rand_val=0.5) is True


def test_xfetch_envelope_serialization_parity() -> None:
    """Verify XFetchEnvelope correctly serializes and deserializes attributes."""
    envelope = XFetchEnvelope(val='{"id": 1, "username": "alice"}', delta=0.042, expiry=1725890000.5)
    json_str = envelope.to_json()
    restored = XFetchEnvelope.from_json(json_str)

    assert restored.val == envelope.val
    assert math.isclose(restored.delta, envelope.delta, rel_tol=1e-5)
    assert math.isclose(restored.expiry, envelope.expiry, rel_tol=1e-5)


# =============================================================================
# 2. XFetch CacheService & UserService Tests
# =============================================================================


@pytest.mark.asyncio
async def test_xfetch_cold_cache_hard_miss(fake_redis: Any) -> None:
    """Test 1: Cold Cache Hard Miss computes value, records delta, and stores padded TTL envelope."""
    cache_service = CacheService(redis_client=fake_redis)
    await cache_service.reset_xfetch_metrics()

    compute_invoked = 0

    async def expensive_compute() -> str:
        nonlocal compute_invoked
        compute_invoked += 1
        await asyncio.sleep(0.01)  # simulate DB query
        return "expensive_result"

    key = "test:xfetch:cold"
    res = await cache_service.xfetch_get_or_compute(key=key, compute_func=expensive_compute, ttl=60.0)

    assert res == "expensive_result"
    assert compute_invoked == 1

    # Verify envelope stored in Redis
    raw_val = await fake_redis.get(key)
    assert raw_val is not None
    envelope = XFetchEnvelope.from_json(str(raw_val))
    assert envelope.val == "expensive_result"
    assert envelope.delta >= 0.005  # measured execution duration
    assert envelope.expiry > time.time()

    # Verify physical TTL in Redis is padded with grace period (60 + max(delta*2, 10) >= 70)
    ttl = await fake_redis.ttl(key)
    assert ttl >= 65

    # Verify telemetry
    metrics = await cache_service.get_xfetch_metrics()
    assert metrics["hard_misses"] == 1
    assert metrics["normal_hits"] == 0
    assert metrics["early_recomputations"] == 0


@pytest.mark.asyncio
async def test_xfetch_warm_cache_normal_hit(fake_redis: Any) -> None:
    """Test 2: Warm Cache Hit returns cached value without executing compute_func."""
    cache_service = CacheService(redis_client=fake_redis)
    await cache_service.reset_xfetch_metrics()

    compute_invoked = 0

    async def compute() -> str:
        nonlocal compute_invoked
        compute_invoked += 1
        return "cached_entity"

    key = "test:xfetch:warm"
    # First call: cold miss
    await cache_service.xfetch_get_or_compute(key=key, compute_func=compute, ttl=300.0)
    assert compute_invoked == 1

    # Second call: warm hit (expiry is 300s in future, should_recompute is False)
    res = await cache_service.xfetch_get_or_compute(
        key=key,
        compute_func=compute,
        ttl=300.0,
        rand_val=0.5,
    )
    assert res == "cached_entity"
    # compute_func should NOT have been invoked again
    assert compute_invoked == 1

    metrics = await cache_service.get_xfetch_metrics()
    assert metrics["hard_misses"] == 1
    assert metrics["normal_hits"] == 1
    assert metrics["early_recomputations"] == 0


@pytest.mark.asyncio
async def test_xfetch_probabilistic_early_refresh(fake_redis: Any) -> None:
    """Test 3: When approaching expiration, probabilistic condition triggers early refresh."""
    cache_service = CacheService(redis_client=fake_redis)
    await cache_service.reset_xfetch_metrics()

    version = 1

    async def compute() -> str:
        return f"payload_v{version}"

    key = "test:xfetch:early"

    # Pre-populate key with logical expiry almost elapsed: expiry = now + 0.05s, delta = 0.1s
    now = time.time()
    stale_envelope = XFetchEnvelope(
        val="payload_v0",
        delta=0.1,
        expiry=now + 0.05,
    )
    await fake_redis.set(key, stale_envelope.to_json(), ex=60)

    # Calling with rand_val=0.5:
    # threshold = - 1.0 * 0.1 * ln(0.5) = 0.0693s
    # remaining_ttl = 0.05s
    # Since 0.0693 > 0.05, should_recompute triggers early refresh!
    res = await cache_service.xfetch_get_or_compute(
        key=key,
        compute_func=compute,
        ttl=120.0,
        beta=1.0,
        now=now,
        rand_val=0.5,
    )

    assert res == "payload_v1"

    # Verify envelope was updated in Redis with new expiry
    raw_val = await fake_redis.get(key)
    assert raw_val is not None
    updated_envelope = XFetchEnvelope.from_json(str(raw_val))
    assert updated_envelope.val == "payload_v1"
    assert updated_envelope.expiry >= now + 115.0

    metrics = await cache_service.get_xfetch_metrics()
    assert metrics["early_recomputations"] == 1
    assert metrics["normal_hits"] == 0


@pytest.mark.asyncio
async def test_xfetch_concurrent_stampede_defeat(fake_redis: Any) -> None:
    """Test 4: Under 50 concurrent requests near expiration, compute_func is executed strictly once or twice.

    48+ requests are served warm data, and the database is completely protected from stampede crash.
    """
    cache_service = CacheService(redis_client=fake_redis)
    await cache_service.reset_xfetch_metrics()

    compute_count = 0

    async def expensive_db_query() -> str:
        nonlocal compute_count
        compute_count += 1
        await asyncio.sleep(0.02)  # 20ms database lock
        return f"db_result_recomputed_{compute_count}"

    key = "test:xfetch:stampede"
    now = time.time()

    # Seed warm envelope in Redis near expiration (0.04s remaining TTL, delta=0.08s)
    envelope = XFetchEnvelope(
        val="initial_warm_value",
        delta=0.08,
        expiry=now + 0.04,
    )
    # Physical TTL still has 30 seconds of grace period
    await fake_redis.set(key, envelope.to_json(), ex=30)

    # Fire 50 concurrent requests
    tasks = [
        cache_service.xfetch_get_or_compute(
            key=key,
            compute_func=expensive_db_query,
            ttl=300.0,
            beta=1.0,
        )
        for _ in range(50)
    ]
    results = await asyncio.gather(*tasks)

    assert len(results) == 50
    # All 50 requests successfully received data
    for val in results:
        assert val in ("initial_warm_value", "db_result_recomputed_1", "db_result_recomputed_2")

    # The database was hit at most twice instead of 50 times!
    assert compute_count <= 2

    # Verify telemetry
    metrics = await cache_service.get_xfetch_metrics()
    assert metrics["total_requests"] == 50
    # The vast majority were normal hits served warm cache
    assert metrics["normal_hits"] >= 45


@pytest.mark.asyncio
async def test_http_user_xfetch_and_telemetry_endpoints(
    fake_redis: Any,
) -> None:
    """Test 5: Full HTTP integration tests for GET /users/{user_id}/xfetch and GET /metrics/xfetch."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        # Step A: Register user
        reg_res = await ac.post(
            "/users/",
            json={
                "email": "xfetch_user@example.com",
                "username": "xfetch_user",
                "password": "SecurePassword123!",
                "password_confirm": "SecurePassword123!",
                "age": 28,
                "role": "user",
            },
        )
        assert reg_res.status_code == 201
        user_id = reg_res.json()["id"]

        # Step B: Fetch user via XFetch endpoint (Cold Hard Miss)
        get_res_1 = await ac.get(f"/users/{user_id}/xfetch")
        assert get_res_1.status_code == 200
        assert get_res_1.json()["username"] == "xfetch_user"

        # Verify key is stored in Redis under xfetch:user:{user_id}
        redis_key = f"{XFETCH_USER_PREFIX}{user_id}"
        assert await fake_redis.exists(redis_key) == 1

        # Step C: Fetch user again via XFetch endpoint (Warm Normal Hit)
        get_res_2 = await ac.get(f"/users/{user_id}/xfetch")
        assert get_res_2.status_code == 200
        assert get_res_2.json()["username"] == "xfetch_user"

        # Step D: Verify telemetry endpoint GET /metrics/xfetch
        metrics_res = await ac.get("/metrics/xfetch")
        assert metrics_res.status_code == 200
        metrics = metrics_res.json()
        assert metrics["hard_misses"] >= 1
        assert metrics["normal_hits"] >= 1
        assert metrics["total_requests"] >= 2
