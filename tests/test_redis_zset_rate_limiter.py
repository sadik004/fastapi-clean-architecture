"""Unit, integration, and concurrency tests for Day 37: Distributed Sliding Window Rate Limiter using Redis ZSET."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.services.rate_limiter_service import RateLimiterService

# =============================================================================
# 1. Pure Service & Algorithmic Unit Tests
# =============================================================================


@pytest.mark.asyncio
async def test_continuous_rolling_expiration(fake_redis: Any) -> None:
    """Test 2: Continuous Rolling Expiration - Advancing simulated time smoothly resets quota."""
    service = RateLimiterService(redis_client=fake_redis)
    key = "user_rolling"
    limit = 3
    window = 5.0  # 5-second sliding window

    # T=100.0: Send 3 requests (hits limit of 3)
    res1 = await service.check_distributed_rate_limit(key, limit=limit, window_seconds=window, now=100.0)
    assert res1[0] is False
    assert res1[1] == 1
    assert res1[2] == 0.0

    res2 = await service.check_distributed_rate_limit(key, limit=limit, window_seconds=window, now=100.5)
    assert res2[0] is False
    assert res2[1] == 2

    res3 = await service.check_distributed_rate_limit(key, limit=limit, window_seconds=window, now=101.0)
    assert res3[0] is False
    assert res3[1] == 3

    # T=102.0: 4th request within the same 5-second window is rejected!
    res4 = await service.check_distributed_rate_limit(key, limit=limit, window_seconds=window, now=102.0)
    assert res4[0] is True  # Limited!
    assert res4[1] == 3  # Quota capped at 3
    # Oldest request was at 100.0, expires at 105.0. At 102.0, retry_after should be 3.0s
    assert pytest.approx(res4[2], rel=1e-2) == 3.0

    # T=105.5: 5.5 seconds later, the request from 100.0 has expired (window is [100.5, 105.5])
    # Active requests remaining: 100.5 and 101.0 (2 requests). A new request is accepted!
    res5 = await service.check_distributed_rate_limit(key, limit=limit, window_seconds=window, now=105.5)
    assert res5[0] is False
    assert res5[1] == 3  # Now active: 100.5, 101.0, 105.5


@pytest.mark.asyncio
async def test_boundary_burst_defect_defeat(fake_redis: Any) -> None:
    """Test 3: Boundary Burst Defeat - Continuous rolling window prevents 2x surge across fixed boundaries."""
    service = RateLimiterService(redis_client=fake_redis)
    key = "user_burst"
    limit = 4
    window = 10.0  # 10-second window

    # In a naive fixed-window limiter with 10s buckets [0-10] and [10-20]:
    # A client could send 4 requests at T=9.0s and another 4 at T=10.1s (8 requests in 2 seconds!).
    # In Redis ZSET sliding window, the rolling horizon [0.1, 10.1] enforces strict capping:

    # Send 3 requests at T=9.0s
    for _ in range(3):
        res = await service.check_distributed_rate_limit(key, limit=limit, window_seconds=window, now=9.0)
        assert res[0] is False

    # Send 1 request at T=10.5s -> Count becomes 4 (allowed)
    res_allowed = await service.check_distributed_rate_limit(key, limit=limit, window_seconds=window, now=10.5)
    assert res_allowed[0] is False
    assert res_allowed[1] == 4

    # Send another request at T=10.6s -> Rolling window [0.6, 10.6] STILL contains all 4 requests! Blocked!
    res_blocked = await service.check_distributed_rate_limit(key, limit=limit, window_seconds=window, now=10.6)
    assert res_blocked[0] is True
    # Retry after is (9.0 + 10.0) - 10.6 = 8.4s
    assert pytest.approx(res_blocked[2], rel=1e-2) == 8.4


@pytest.mark.asyncio
async def test_concurrent_race_condition_defeat(fake_redis: Any) -> None:
    """Test 4: Concurrent Race Condition - 20 concurrent async requests against limit 10: exactly 10 succeed, 10 reject."""
    service = RateLimiterService(redis_client=fake_redis)
    key = "concurrent_client"
    limit = 10
    window = 60.0
    now = 500.0

    # Fire 20 concurrent requests simultaneously using asyncio.gather
    tasks = [service.check_distributed_rate_limit(key, limit=limit, window_seconds=window, now=now) for _ in range(20)]
    results = await asyncio.gather(*tasks)

    allowed_count = sum(1 for is_limited, _, _ in results if not is_limited)
    rejected_count = sum(1 for is_limited, _, _ in results if is_limited)

    assert allowed_count == 10, f"Expected exactly 10 allowed requests, got {allowed_count}"
    assert rejected_count == 10, f"Expected exactly 10 rejected requests, got {rejected_count}"

    # Verify cardinality in Redis is capped at 10
    status = await service.get_client_status(key, window_seconds=window, now=now)
    assert status["active_requests"] == 10


# =============================================================================
# 2. REST API Integration & RFC 429 Header Tests
# =============================================================================


@pytest.mark.asyncio
async def test_quota_enforcement_and_retry_after_headers(fake_redis: Any) -> None:
    """Test 1: Quota Enforcement - Limit 5 req / 10s: first 5 return 200, 6th returns 429 with RFC headers."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        headers = {"X-API-Key": "test_client_alpha"}

        # Send 5 valid requests
        for i in range(1, 6):
            response = await ac.get("/test-rate-limit/distributed", headers=headers)
            assert response.status_code == 200, f"Request {i} failed with {response.status_code}"
            data = response.json()
            assert data["message"] == "Request accepted within distributed sliding window quota"
            assert data["client_id"] == "test_client_alpha"

        # 6th request must be rejected with HTTP 429
        rejected_response = await ac.get("/test-rate-limit/distributed", headers=headers)
        assert rejected_response.status_code == 429
        assert "Retry-After" in rejected_response.headers
        assert rejected_response.headers["X-RateLimit-Limit"] == "5"
        assert rejected_response.headers["X-RateLimit-Remaining"] == "0"

        retry_after = int(rejected_response.headers["Retry-After"])
        assert 1 <= retry_after <= 11


@pytest.mark.asyncio
async def test_live_rate_limit_metrics_endpoint(fake_redis: Any) -> None:
    """Test 5: Live Metrics Inspection - GET /metrics/rate-limit/{client_id} returns active requests & TTL."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        client_key = "test:client_beta"
        headers = {"X-API-Key": "client_beta"}

        # Fire 3 requests
        for _ in range(3):
            res = await ac.get("/test-rate-limit/distributed", headers=headers)
            assert res.status_code == 200

        # Query metrics probe
        metrics_res = await ac.get(f"/metrics/rate-limit/{client_key}?window_seconds=10.0")
        assert metrics_res.status_code == 200
        metrics_data = metrics_res.json()

        assert metrics_data["key"] == f"ratelimit:{client_key}"
        assert metrics_data["active_requests"] == 3
        assert metrics_data["window_seconds"] == 10.0
        assert metrics_data["ttl_seconds"] > 0
        assert metrics_data["oldest_timestamp"] is not None
        assert metrics_data["newest_timestamp"] is not None
