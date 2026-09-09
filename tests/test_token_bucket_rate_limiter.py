"""Unit, integration, and high-concurrency atomic Lua tests for Day 38: Token Bucket Rate Limiter."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.services.rate_limiter_service import RateLimiterService

# =============================================================================
# 1. Pure Service & Mathematical Unit Tests
# =============================================================================


@pytest.mark.asyncio
async def test_burst_allowance_service(fake_redis: Any) -> None:
    """Test 1: Burst Allowance - Bucket with capacity=5 allows 5 immediate requests without throttling."""
    service = RateLimiterService(redis_client=fake_redis)
    key = "burst_client"
    capacity = 5.0
    refill_rate = 1.0
    now = 1000.0

    # Fire 5 immediate requests consuming 1.0 token each
    for i in range(1, 6):
        is_limited, remaining, retry_after = await service.check_token_bucket(
            key=key, capacity=capacity, refill_rate=refill_rate, requested=1.0, now=now
        )
        assert is_limited is False, f"Request {i} was prematurely throttled"
        assert remaining == capacity - i
        assert retry_after == 0.0


@pytest.mark.asyncio
async def test_throttling_after_burst(fake_redis: Any) -> None:
    """Test 2: Throttling After Burst - 6th immediate request is rejected with accurate retry_after."""
    service = RateLimiterService(redis_client=fake_redis)
    key = "throttle_client"
    capacity = 5.0
    refill_rate = 1.0
    now = 2000.0

    # Exhaust all 5 tokens
    for _ in range(5):
        is_limited, _, _ = await service.check_token_bucket(
            key=key, capacity=capacity, refill_rate=refill_rate, requested=1.0, now=now
        )
        assert is_limited is False

    # 6th request at the exact same timestamp must be throttled
    is_limited, remaining, retry_after = await service.check_token_bucket(
        key=key, capacity=capacity, refill_rate=refill_rate, requested=1.0, now=now
    )
    assert is_limited is True
    assert remaining == 0.0
    # To get 1 token at 1.0 token/s, client must wait 1.0 second
    assert pytest.approx(retry_after, rel=1e-2) == 1.0


@pytest.mark.asyncio
async def test_token_refill_mechanism(fake_redis: Any) -> None:
    """Test 3: Token Refill - Advancing time by 2.0s allows exactly 2 new requests."""
    service = RateLimiterService(redis_client=fake_redis)
    key = "refill_client"
    capacity = 5.0
    refill_rate = 1.0
    now = 3000.0

    # Exhaust all 5 tokens at T=3000.0
    for _ in range(5):
        await service.check_token_bucket(key=key, capacity=capacity, refill_rate=refill_rate, requested=1.0, now=now)

    # Verify exhausted at T=3000.0
    is_limited, _, _ = await service.check_token_bucket(
        key=key, capacity=capacity, refill_rate=refill_rate, requested=1.0, now=now
    )
    assert is_limited is True

    # Advance time by 2.0 seconds -> 2 tokens replenished (T=3002.0)
    now_refilled = now + 2.0

    # Request 1 of refilled quota
    lim1, rem1, _ = await service.check_token_bucket(
        key=key, capacity=capacity, refill_rate=refill_rate, requested=1.0, now=now_refilled
    )
    assert lim1 is False
    assert pytest.approx(rem1, rel=1e-2) == 1.0

    # Request 2 of refilled quota
    lim2, rem2, _ = await service.check_token_bucket(
        key=key, capacity=capacity, refill_rate=refill_rate, requested=1.0, now=now_refilled
    )
    assert lim2 is False
    assert pytest.approx(rem2, rel=1e-2) == 0.0

    # Request 3 should now be throttled
    lim3, _, retry3 = await service.check_token_bucket(
        key=key, capacity=capacity, refill_rate=refill_rate, requested=1.0, now=now_refilled
    )
    assert lim3 is True
    assert pytest.approx(retry3, rel=1e-2) == 1.0


@pytest.mark.asyncio
async def test_high_concurrency_atomic_lua_defeat(fake_redis: Any) -> None:
    """Test 4: Concurrency Race Condition - 30 concurrent async requests against capacity 10 with 0 refill:

    EXACTLY 10 succeed, EXACTLY 20 are rejected, proving strict Lua atomicity.
    """
    service = RateLimiterService(redis_client=fake_redis)
    key = "concurrent_token_client"
    capacity = 10.0
    refill_rate = 0.0  # Zero refill so no new tokens generate during concurrent burst
    now = 4000.0

    # Fire 30 concurrent async requests simultaneously
    tasks = [
        service.check_token_bucket(key=key, capacity=capacity, refill_rate=refill_rate, requested=1.0, now=now)
        for _ in range(30)
    ]
    results = await asyncio.gather(*tasks)

    allowed = sum(1 for is_limited, _, _ in results if not is_limited)
    rejected = sum(1 for is_limited, _, _ in results if is_limited)

    assert allowed == 10, f"Expected exactly 10 allowed requests, got {allowed}"
    assert rejected == 20, f"Expected exactly 20 rejected requests, got {rejected}"

    # Verify status probe reflects empty bucket
    status = await service.get_token_bucket_status(key=key, capacity=capacity, refill_rate=refill_rate, now=now)
    assert status["tokens"] == 0.0


# =============================================================================
# 2. HTTP Integration, RFC Headers & Telemetry Tests
# =============================================================================


@pytest.mark.asyncio
async def test_http_token_bucket_endpoint_and_headers(fake_redis: Any) -> None:
    """Test 5: HTTP Endpoint & RFC Headers - First 5 return 200, 6th returns 429 with RFC headers."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        headers = {"X-API-Key": "http_client_token"}

        # 5 allowed requests
        for i in range(1, 6):
            response = await ac.get("/test-rate-limit/token-bucket", headers=headers)
            assert response.status_code == 200, f"Request {i} failed: {response.text}"
            data = response.json()
            assert data["message"] == "Request accepted within token bucket quota"
            assert data["client_id"] == "http_client_token"

        # 6th request must trigger HTTP 429
        throttled_response = await ac.get("/test-rate-limit/token-bucket", headers=headers)
        assert throttled_response.status_code == 429
        assert "Retry-After" in throttled_response.headers
        assert throttled_response.headers["X-RateLimit-Limit"] == "5"
        assert throttled_response.headers["X-RateLimit-Remaining"] == "0"
        retry_after = int(throttled_response.headers["Retry-After"])
        assert retry_after >= 1


@pytest.mark.asyncio
async def test_metrics_token_bucket_endpoint(fake_redis: Any) -> None:
    """Test 6: Telemetry Probe - GET /metrics/token-bucket/{client_id} returns live tokens and TTL."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        client_id = "telemetry_user_99"
        headers = {"X-API-Key": client_id}

        # Fire 2 requests
        for _ in range(2):
            res = await ac.get("/test-rate-limit/token-bucket", headers=headers)
            assert res.status_code == 200

        # Query metrics
        metrics_res = await ac.get(f"/metrics/token-bucket/test:{client_id}?capacity=5.0&refill_rate=1.0")
        assert metrics_res.status_code == 200
        metrics_data = metrics_res.json()

        assert metrics_data["key"] == f"ratelimit:tokenbucket:test:{client_id}"
        assert metrics_data["capacity"] == 5.0
        assert metrics_data["refill_rate"] == 1.0
        assert metrics_data["tokens"] <= 3.05  # 5 - 2 = 3 tokens remaining
        assert metrics_data["ttl_seconds"] > 0
        assert metrics_data["last_updated"] is not None
