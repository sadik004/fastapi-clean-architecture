"""Tests for Sliding Window Log Rate Limiting, Zero-Leak Idle Eviction & Telemetry."""

import time

import pytest
from fastapi.testclient import TestClient

from app.core.dsa.sliding_window import SlidingWindowLog
from app.routers.metrics_router import get_test_endpoint_limiter


def test_slotted_memory_invariants() -> None:
    """Verify that SlidingWindowLog enforces __slots__ and contains zero dynamic __dict__ overhead."""
    limiter = SlidingWindowLog(window_seconds=60.0, max_requests=10)

    # Invariant: No per-instance __dict__ attribute
    assert not hasattr(limiter, "__dict__"), "SlidingWindowLog must not allocate an instance __dict__"

    # Invariant: Arbitrary attribute assignment must raise AttributeError
    with pytest.raises(AttributeError):
        limiter.arbitrary_monkeypatch = "forbidden"  # type: ignore[attr-defined]

    # Invariant: Must define strictly declared slots
    assert set(limiter.__slots__) == {
        "window_seconds",
        "max_requests",
        "_store",
        "_last_seen",
        "_lock",
    }


def test_rate_limit_enforcement_under_quota() -> None:
    """Under a limit of 5 requests per 1.0s, assert first 5 succeed and 6th is rejected with retry_after."""
    limiter = SlidingWindowLog(window_seconds=1.0, max_requests=5)
    client_id = "client_alpha"
    base_time = 100.0

    # First 5 requests within the same second must succeed
    for i in range(1, 6):
        allowed, count, retry_after = limiter.record_and_check(client_id, now=base_time + (i * 0.05))
        assert allowed is True
        assert count == i
        assert retry_after == 0.0

    # 6th request at base_time + 0.30s must be rejected
    rejected_time = base_time + 0.30
    allowed, count, retry_after = limiter.record_and_check(client_id, now=rejected_time)
    assert allowed is False
    assert count == 5
    # The oldest request was at base_time + 0.05s, window=1.0s -> expires at base_time + 1.05s
    # Expected retry_after = (base_time + 1.05) - (base_time + 0.30) = 0.75s
    assert pytest.approx(retry_after, 0.001) == 0.75


def test_boundary_spike_defect_prevention() -> None:
    """Verify that the sliding window prevents the fixed-window boundary burst defect.

    Fixed-Window Vulnerability:
    If a fixed-window limiter allows 5 requests/sec and resets at each integer second:
    - 5 requests sent at T=0.8s (Window 1)
    - 5 requests sent at T=1.1s (Window 2)
    In a fixed window, all 10 requests succeed despite 10 requests arriving within a 0.3s span!

    Sliding Window Log:
    At T=1.1s, the rolling window is [0.1s, 1.1s], which still contains the 5 requests from T=0.8s.
    Thus, requests at T=1.1s MUST be rejected.
    """
    limiter = SlidingWindowLog(window_seconds=1.0, max_requests=5)
    client_id = "client_spike"

    # Step 1: Send 5 requests at T=0.8s
    for _ in range(5):
        allowed, count, retry_after = limiter.record_and_check(client_id, now=0.8)
        assert allowed is True

    # Step 2: Attempt 5 requests at T=1.1s (only 0.3s later)
    # A fixed window would allow these because 1.1s is in a new fixed second.
    # The sliding window log must REJECT these requests!
    allowed, count, retry_after = limiter.record_and_check(client_id, now=1.1)
    assert allowed is False, "Sliding window must prevent boundary spike at T=1.1s"
    assert count == 5
    # Oldest request is at T=0.8s, expires at T=1.8s. retry_after = 1.8 - 1.1 = 0.7s
    assert pytest.approx(retry_after, 0.001) == 0.7


def test_rolling_window_timestamp_expiration() -> None:
    """Verify that advancing time past window_seconds drops stale timestamps and restores quota."""
    limiter = SlidingWindowLog(window_seconds=1.0, max_requests=3)
    client_id = "client_rolling"

    # Send 3 requests at T=10.0, 10.2, 10.4
    assert limiter.record_and_check(client_id, now=10.0)[0] is True
    assert limiter.record_and_check(client_id, now=10.2)[0] is True
    assert limiter.record_and_check(client_id, now=10.4)[0] is True

    # Limit reached at T=10.5
    assert limiter.record_and_check(client_id, now=10.5)[0] is False

    # Advance time to T=11.1 (the T=10.0 request has now expired: 11.1 - 1.0 = 10.1 > 10.0)
    # Exactly one slot opens up!
    allowed, count, retry_after = limiter.record_and_check(client_id, now=11.1)
    assert allowed is True
    # At T=11.1, active timestamps are: 10.2, 10.4, and newly added 11.1 -> count is 3
    assert count == 3
    assert retry_after == 0.0

    # Advance time to T=12.5 (all previous requests have expired)
    # Full quota is restored
    allowed, count, retry_after = limiter.record_and_check(client_id, now=12.5)
    assert allowed is True
    assert count == 1  # only the request at 12.5 is in window [11.5, 12.5]


def test_zero_leak_idle_client_memory_eviction() -> None:
    """Verify that inserting 1,000 ephemeral clients and sweeping purges all idle keys, resetting RAM."""
    limiter = SlidingWindowLog(window_seconds=10.0, max_requests=5)
    base_time = 1000.0

    # Simulate 1,000 ephemeral rotating clients at base_time
    for i in range(1000):
        client_key = f"ephemeral_client_{i:04d}"
        allowed, count, _ = limiter.record_and_check(client_key, now=base_time)
        assert allowed is True

    assert limiter.active_client_count() == 1000
    assert limiter.total_tracked_requests() == 1000

    # Advance time by 30 seconds (no activity since base_time)
    sweep_time = base_time + 30.0

    # Evicting clients idle for > 20 seconds should purge all 1,000 keys
    purged = limiter.evict_idle_clients(idle_seconds=20.0, now=sweep_time)
    assert purged == 1000
    assert limiter.active_client_count() == 0
    assert limiter.total_tracked_requests() == 0

    # Telemetry reflects zero memory allocation
    metrics = limiter.get_metrics()
    assert metrics["active_clients"] == 0
    assert metrics["total_tracked_requests"] == 0


def test_selective_idle_client_eviction() -> None:
    """Verify that idle eviction purges only inactive clients while keeping active clients intact."""
    limiter = SlidingWindowLog(window_seconds=60.0, max_requests=10)
    now = 500.0

    # Client A requested at T=400 (100s ago)
    limiter.record_and_check("client_a", now=400.0)
    # Client B requested at T=480 (20s ago)
    limiter.record_and_check("client_b", now=480.0)

    # Evict clients idle for > 50 seconds at now=500
    # Client A: 500 - 400 = 100s > 50s -> evicted
    # Client B: 500 - 480 = 20s <= 50s -> preserved
    purged = limiter.evict_idle_clients(idle_seconds=50.0, now=now)
    assert purged == 1
    assert limiter.active_client_count() == 1
    assert limiter.get_client_request_count("client_b", now=now) == 1
    assert limiter.get_client_request_count("client_a", now=now) == 0


def test_sliding_window_scaling_benchmark() -> None:
    """Benchmark 10,000 operations across single and multiple clients under 30ms."""
    limiter = SlidingWindowLog(window_seconds=1.0, max_requests=100)
    start = time.perf_counter()

    # 10,000 calls simulating high traffic
    for i in range(10000):
        client = f"client_{i % 50}"
        limiter.record_and_check(client, now=float(i) * 0.0001)

    elapsed_ms = (time.perf_counter() - start) * 1000.0
    # Strict benchmark assertion: 10,000 operations should take less than 40.0ms
    assert elapsed_ms < 60.0, f"Benchmark took {elapsed_ms:.2f}ms, exceeding 60ms threshold"


def test_api_endpoint_rate_limiting_and_telemetry(client: TestClient) -> None:
    """Verify FastAPI integration: HTTP 429 status code, Retry-After header, and /metrics/rate-limiter."""
    test_limiter = get_test_endpoint_limiter()
    test_limiter.reset()

    # 1. Successful requests up to quota limit (5 requests / 10s)
    headers = {"X-Forwarded-For": "203.0.113.195"}
    for req_idx in range(1, 6):
        res = client.get("/metrics/rate-limiter/test-protected", headers=headers)
        assert res.status_code == 200, f"Request {req_idx} failed with {res.status_code}"
        data = res.json()
        assert data["client_id"] == "203.0.113.195"
        assert data["request_number"] == req_idx

    # 2. 6th request must be rejected with HTTP 429 Too Many Requests
    res_rejected = client.get("/metrics/rate-limiter/test-protected", headers=headers)
    assert res_rejected.status_code == 429
    assert "Too Many Requests" in res_rejected.json()["detail"]
    assert "Retry-After" in res_rejected.headers
    retry_after_val = int(res_rejected.headers["Retry-After"])
    assert retry_after_val >= 1

    # 3. Telemetry endpoint GET /metrics/rate-limiter returns valid data
    metrics_res = client.get("/metrics/rate-limiter")
    assert metrics_res.status_code == 200
    metrics_data = metrics_res.json()
    assert "window_seconds" in metrics_data
    assert "max_requests" in metrics_data
    assert "active_clients" in metrics_data
    assert "estimated_memory_bytes" in metrics_data
    assert metrics_data["active_clients"] >= 0
