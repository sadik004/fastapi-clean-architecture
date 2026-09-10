"""Unit and integration tests for Exponential Backoff with Jitter (Day 63).

Validates:
1. Mathematical Bounds & Uniform Variance of Full Jitter
2. Equal Jitter and Decorrelated Jitter Bounds
3. Async Retry Decorator Transient Recovery & Max Retries Exhaustion
4. Non-retriable exception bypass
5. HTTP Endpoints for Retry Simulation and Distribution Analysis
"""

from __future__ import annotations

import statistics

import httpx
import pytest

from app.core.exceptions import ServiceUnavailableException
from app.core.resilience.backoff import calculate_backoff, retry_with_backoff
from app.main import app
from app.services.resilient_third_party_service import ResilientThirdPartyService

# ==============================================================================
# 1. Mathematical & Algorithmic Bounds Verification
# ==============================================================================


def test_full_jitter_mathematical_bounds_and_variance() -> None:
    """Verify 1,000 full jitter samples fall within [0, base * 2^attempt] and exhibit variance."""
    attempt = 3
    base_delay = 1.0
    max_delay = 60.0
    upper_bound = base_delay * (2**attempt)  # 8.0s

    samples = [
        calculate_backoff(
            attempt=attempt,
            base_delay=base_delay,
            max_delay=max_delay,
            strategy="full_jitter",
        )
        for _ in range(1000)
    ]

    # Every sample must be strictly bounded in [0, upper_bound]
    for val in samples:
        assert 0.0 <= val <= upper_bound

    # Must exhibit non-zero statistical variance (proves randomization is active)
    variance = statistics.variance(samples)
    assert variance > 0.5

    # Mean for uniform distribution in [0, 8.0] should be close to 4.0
    mean_val = statistics.mean(samples)
    assert 3.0 <= mean_val <= 5.0


def test_backoff_capping_at_max_delay() -> None:
    """Verify exponential delay is strictly clamped by max_delay when 2^attempt exceeds it."""
    attempt = 10  # 2^10 = 1024
    base_delay = 1.0
    max_delay = 15.0

    samples = [
        calculate_backoff(
            attempt=attempt,
            base_delay=base_delay,
            max_delay=max_delay,
            strategy="full_jitter",
        )
        for _ in range(200)
    ]

    for val in samples:
        assert 0.0 <= val <= max_delay


def test_equal_jitter_bounds() -> None:
    """Verify equal jitter guarantees minimum sleep of temp/2 and randomizes remaining half."""
    attempt = 2
    base_delay = 1.0
    max_delay = 30.0
    temp = base_delay * (2**attempt)  # 4.0s
    expected_min = temp / 2.0  # 2.0s
    expected_max = temp  # 4.0s

    samples = [
        calculate_backoff(
            attempt=attempt,
            base_delay=base_delay,
            max_delay=max_delay,
            strategy="equal_jitter",
        )
        for _ in range(500)
    ]

    for val in samples:
        assert expected_min <= val <= expected_max


def test_decorrelated_jitter_bounds() -> None:
    """Verify decorrelated jitter scales dynamically from previous delay without exceeding max_delay."""
    base_delay = 0.5
    max_delay = 10.0
    prev: float | None = None

    for attempt in range(5):
        val = calculate_backoff(
            attempt=attempt,
            base_delay=base_delay,
            max_delay=max_delay,
            strategy="decorrelated_jitter",
            previous_delay=prev,
        )
        assert base_delay <= val <= max_delay
        prev = val


def test_no_jitter_deterministic_calculation() -> None:
    """Verify deterministic no_jitter calculation returns exactly min(max_delay, base * 2^attempt)."""
    assert calculate_backoff(0, base_delay=0.5, max_delay=10.0, strategy="no_jitter") == 0.5
    assert calculate_backoff(1, base_delay=0.5, max_delay=10.0, strategy="no_jitter") == 1.0
    assert calculate_backoff(2, base_delay=0.5, max_delay=10.0, strategy="no_jitter") == 2.0
    assert calculate_backoff(5, base_delay=0.5, max_delay=10.0, strategy="no_jitter") == 10.0  # capped


def test_invalid_strategy_raises_value_error() -> None:
    """Verify unsupported strategy names raise descriptive ValueError."""
    with pytest.raises(ValueError, match="Unsupported backoff strategy"):
        calculate_backoff(1, strategy="unknown_strategy")


# ==============================================================================
# 2. Async Retry Decorator Verification
# ==============================================================================


@pytest.mark.asyncio
async def test_retry_decorator_transient_success() -> None:
    """Verify decorator retries transient failures and returns success when operation recovers."""
    call_count = 0
    retried_attempts: list[int] = []

    def on_retry(att: int, delay: float, exc: Exception) -> None:
        retried_attempts.append(att)

    @retry_with_backoff(
        max_retries=3,
        base_delay=0.001,
        max_delay=0.05,
        strategy="full_jitter",
        on_retry=on_retry,
    )
    async def transient_operation() -> str:
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            raise ServiceUnavailableException("Transient downstream failure")
        return "operation_succeeded"

    result = await transient_operation()

    assert result == "operation_succeeded"
    assert call_count == 3
    assert retried_attempts == [1, 2]


@pytest.mark.asyncio
async def test_retry_decorator_max_retries_exhaustion() -> None:
    """Verify decorator re-raises original exception after exhausting max_retries."""
    call_count = 0

    @retry_with_backoff(
        max_retries=2,
        base_delay=0.001,
        max_delay=0.02,
        strategy="equal_jitter",
    )
    async def permanently_failing_operation() -> None:
        nonlocal call_count
        call_count += 1
        raise ServiceUnavailableException("Persistent dependency outage")

    with pytest.raises(ServiceUnavailableException, match="Persistent dependency outage"):
        await permanently_failing_operation()

    # Initial call (1) + 2 retries = 3 total invocations
    assert call_count == 3


@pytest.mark.asyncio
async def test_retry_decorator_bypasses_non_retriable_exceptions() -> None:
    """Verify non-retriable exceptions are not caught and bubble up immediately without retries."""
    call_count = 0

    @retry_with_backoff(
        max_retries=3,
        base_delay=0.001,
        retry_exceptions=(ServiceUnavailableException,),
    )
    async def invalid_arg_operation() -> None:
        nonlocal call_count
        call_count += 1
        raise ValueError("Invalid client input")

    with pytest.raises(ValueError, match="Invalid client input"):
        await invalid_arg_operation()

    # Must fail on the very first attempt
    assert call_count == 1


# ==============================================================================
# 3. Resilient Third-Party Service Verification
# ==============================================================================


@pytest.mark.asyncio
async def test_resilient_third_party_service_simulation() -> None:
    """Verify service simulates transient failures and tracks delays."""
    service = ResilientThirdPartyService()
    service.reset()

    res = await service.execute_simulated_call(
        target_id="payment_gw_sim",
        failures_before_success=2,
        max_retries=3,
        base_delay=0.001,
        max_delay=0.01,
        strategy="full_jitter",
    )

    assert res["success"] is True
    assert res["attempts_made"] == 3
    assert res["retries_count"] == 2
    assert len(res["delays"]) == 2
    assert res["result"]["status"] == "success"


# ==============================================================================
# 4. HTTP API Endpoints Integration Verification
# ==============================================================================


@pytest.mark.asyncio
async def test_http_simulate_retry_success() -> None:
    """Verify POST /resilience/backoff/simulate-retry succeeds on transient failure."""
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/resilience/backoff/simulate-retry",
            json={
                "target_id": "test_sms_provider",
                "failures_before_success": 2,
                "max_retries": 3,
                "base_delay": 0.005,
                "max_delay": 0.05,
                "strategy": "full_jitter",
            },
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["target_id"] == "test_sms_provider"
        assert data["success"] is True
        assert data["attempts_made"] == 3
        assert data["retries_count"] == 2
        assert len(data["delays"]) == 2


@pytest.mark.asyncio
async def test_http_simulate_retry_exhaustion_503() -> None:
    """Verify POST /resilience/backoff/simulate-retry returns 503 when retries are exhausted."""
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/resilience/backoff/simulate-retry",
            json={
                "target_id": "test_exhaust_service",
                "failures_before_success": 4,
                "max_retries": 2,
                "base_delay": 0.005,
                "max_delay": 0.05,
                "strategy": "full_jitter",
            },
        )

        assert resp.status_code == 503
        data = resp.json()
        assert data["error"]["code"] == "DOWNSTREAM_TEMPORARILY_UNAVAILABLE"


@pytest.mark.asyncio
async def test_http_backoff_distribution_endpoint() -> None:
    """Verify GET /resilience/backoff/distribution returns sample statistics."""
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            "/resilience/backoff/distribution",
            params={
                "attempt": 3,
                "base_delay": 0.1,
                "max_delay": 5.0,
                "strategy": "full_jitter",
                "samples": 50,
            },
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["attempt"] == 3
        assert data["strategy"] == "full_jitter"
        assert data["upper_bound"] == 0.8  # 0.1 * 2^3 = 0.8
        assert data["samples"] == 50
        assert len(data["delays"]) == 50
        assert 0.0 <= data["min_delay"] <= data["max_calculated_delay"] <= 0.8
