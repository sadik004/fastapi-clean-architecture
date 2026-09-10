"""Comprehensive test suite for Day 61: Circuit Breaker Pattern Architecture.

Verifies:
1. Closed State Normal Execution: Successful calls pass without state mutation.
2. Failure Threshold Tripping: Exactly 5 consecutive failures trip state from CLOSED to OPEN.
3. Fail-Fast Verification: When OPEN, target function is NEVER executed; raises CircuitBreakerOpenException (HTTP 503).
4. Half-Open Probe & Recovery: Simulated clock advances past recovery_timeout; state transitions to HALF_OPEN;
   consecutive probe successes reset state back to CLOSED.
5. Half-Open Failure Re-Tripping: Any failure in HALF_OPEN immediately re-trips state to OPEN and resets timer.
6. HTTP Integration & Telemetry: Validates /resilience/circuit-breaker/charge, /metrics/circuit-breaker,
   HTTP 503 response envelope, and 'Retry-After' header injection.
7. Synchronous Execution & Input Boundary: Verifies sync execution, decorator, and validation guards.
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.exceptions import CircuitBreakerOpenException, ServiceUnavailableException
from app.core.resilience.circuit_breaker import CircuitBreaker, CircuitState
from app.main import app
from app.services.resilient_payment_service import (
    ResilientPaymentService,
    get_resilient_payment_service,
)


class MockClock:
    """Deterministic monotonic clock simulator for testing recovery timeouts without sleeping."""

    def __init__(self, initial_time: float = 1000.0) -> None:
        self._current_time = initial_time

    def __call__(self) -> float:
        return self._current_time

    def advance(self, seconds: float) -> None:
        self._current_time += seconds


def _get_state(cb: CircuitBreaker) -> CircuitState:
    """Retrieve dynamic circuit breaker state without triggering mypy static property narrowing."""
    return cb.state


# =============================================================================
# 1. Pure Circuit Breaker Engine Unit Tests
# =============================================================================


@pytest.mark.asyncio
async def test_closed_state_normal_execution() -> None:
    """Verify that under healthy conditions, calls execute normally in CLOSED state."""
    clock = MockClock()
    cb = CircuitBreaker(
        name="test_service",
        failure_threshold=5,
        recovery_timeout=30.0,
        half_open_success_threshold=2,
        time_provider=clock,
    )

    invocations = 0

    async def healthy_target(val: int) -> int:
        nonlocal invocations
        invocations += 1
        return val * 2

    assert cb.state == CircuitState.CLOSED
    assert cb.consecutive_failures == 0

    # Execute multiple successful calls
    for i in range(1, 6):
        res = await cb.execute_async(healthy_target, val=i)
        assert res == i * 2

    assert invocations == 5
    assert cb.state == CircuitState.CLOSED
    assert cb.consecutive_failures == 0
    assert cb.get_metrics()["total_successes"] == 5
    assert cb.get_metrics()["total_failures"] == 0


@pytest.mark.asyncio
async def test_failure_threshold_tripping() -> None:
    """Verify that exactly failure_threshold (5) consecutive failures trip CLOSED -> OPEN."""
    clock = MockClock()
    cb = CircuitBreaker(
        name="failing_service",
        failure_threshold=5,
        recovery_timeout=30.0,
        half_open_success_threshold=2,
        time_provider=clock,
    )

    async def faulty_target() -> None:
        raise ConnectionResetError("Downstream socket disconnected")

    # 1 to 4 failures should keep breaker CLOSED
    for attempt in range(1, 5):
        with pytest.raises(ConnectionResetError):
            await cb.execute_async(faulty_target)
        assert cb.state == CircuitState.CLOSED
        assert cb.consecutive_failures == attempt

    # 5th failure must trip the breaker to OPEN
    with pytest.raises(ConnectionResetError):
        await cb.execute_async(faulty_target)

    assert cb.state == CircuitState.OPEN
    assert cb.consecutive_failures == 5
    assert cb.remaining_recovery_time == 30.0


@pytest.mark.asyncio
async def test_fail_fast_open_state() -> None:
    """Verify that when OPEN, subsequent calls fail-fast without invoking target function."""
    clock = MockClock()
    cb = CircuitBreaker(
        name="tripped_service",
        failure_threshold=3,
        recovery_timeout=20.0,
        time_provider=clock,
    )

    target_executed = 0

    async def faulty_target() -> None:
        nonlocal target_executed
        target_executed += 1
        raise TimeoutError("Downstream timeout")

    # Trip breaker
    for _ in range(3):
        with pytest.raises(TimeoutError):
            await cb.execute_async(faulty_target)

    assert cb.state == CircuitState.OPEN
    assert target_executed == 3

    # Now verify fail-fast behavior: target must NOT be executed!
    for _ in range(5):
        with pytest.raises(CircuitBreakerOpenException) as exc_info:
            await cb.execute_async(faulty_target)

        assert exc_info.value.code == "CIRCUIT_BREAKER_OPEN"
        assert exc_info.value.recovery_timeout == 20.0
        assert isinstance(exc_info.value, ServiceUnavailableException)

    # Target call count remains 3 (ZERO downstream calls were attempted)
    assert target_executed == 3
    assert cb.get_metrics()["total_short_circuits"] == 5


@pytest.mark.asyncio
async def test_half_open_probe_and_recovery() -> None:
    """Verify that advancing clock past recovery_timeout allows probes and successful recovery."""
    clock = MockClock(1000.0)
    cb = CircuitBreaker(
        name="probe_service",
        failure_threshold=2,
        recovery_timeout=15.0,
        half_open_success_threshold=2,
        time_provider=clock,
    )

    async def faulty_target() -> None:
        raise RuntimeError("Transient outage")

    async def recovered_target() -> str:
        return "recovered"

    # Trip breaker
    for _ in range(2):
        with pytest.raises(RuntimeError):
            await cb.execute_async(faulty_target)

    assert cb.state == CircuitState.OPEN

    # Advance clock by 10s (not expired yet, 5s remaining)
    clock.advance(10.0)
    assert _get_state(cb) == CircuitState.OPEN
    assert cb.remaining_recovery_time == 5.0

    # Advance clock another 6s (total 16s elapsed >= 15s)
    clock.advance(6.0)
    assert _get_state(cb) == CircuitState.HALF_OPEN
    assert cb.remaining_recovery_time == 0.0

    # Probe 1 succeeds
    res1 = await cb.execute_async(recovered_target)
    assert res1 == "recovered"
    assert _get_state(cb) == CircuitState.HALF_OPEN
    assert cb.consecutive_successes == 1

    # Probe 2 succeeds -> meets threshold of 2! Resets to CLOSED!
    res2 = await cb.execute_async(recovered_target)
    assert res2 == "recovered"
    assert _get_state(cb) == CircuitState.CLOSED
    assert cb.consecutive_failures == 0
    assert cb.consecutive_successes == 0


@pytest.mark.asyncio
async def test_half_open_failure_retripping() -> None:
    """Verify that any failure in HALF_OPEN immediately re-trips state back to OPEN."""
    clock = MockClock(1000.0)
    cb = CircuitBreaker(
        name="retrip_service",
        failure_threshold=2,
        recovery_timeout=25.0,
        half_open_success_threshold=3,
        time_provider=clock,
    )

    async def fail_call() -> None:
        raise ValueError("Service still down")

    async def succeed_call() -> str:
        return "ok"

    # Trip breaker
    for _ in range(2):
        with pytest.raises(ValueError):
            await cb.execute_async(fail_call)

    assert cb.state == CircuitState.OPEN

    # Advance past recovery timeout
    clock.advance(26.0)
    assert _get_state(cb) == CircuitState.HALF_OPEN

    # Probe 1 succeeds
    await cb.execute_async(succeed_call)
    assert _get_state(cb) == CircuitState.HALF_OPEN
    assert cb.consecutive_successes == 1

    # Probe 2 fails! Immediate re-trip to OPEN
    with pytest.raises(ValueError, match="down"):
        await cb.execute_async(fail_call)

    assert _get_state(cb) == CircuitState.OPEN
    assert cb.consecutive_failures == 1
    assert cb.consecutive_successes == 0
    assert cb.remaining_recovery_time == 25.0

    # Fail-fast should now be enforced for the new window
    with pytest.raises(CircuitBreakerOpenException):
        await cb.execute_async(succeed_call)


def test_sync_circuit_breaker_execution() -> None:
    """Verify CircuitBreaker works with synchronous functions via execute()."""
    clock = MockClock()
    cb = CircuitBreaker(
        name="sync_service",
        failure_threshold=2,
        recovery_timeout=10.0,
        time_provider=clock,
    )

    def sync_add(a: int, b: int) -> int:
        return a + b

    assert cb.execute(sync_add, a=10, b=20) == 30

    def sync_fail() -> None:
        raise ZeroDivisionError("Cannot divide by zero")

    with pytest.raises(ZeroDivisionError):
        cb.execute(sync_fail)
    with pytest.raises(ZeroDivisionError):
        cb.execute(sync_fail)

    assert cb.state == CircuitState.OPEN

    with pytest.raises(CircuitBreakerOpenException):
        cb.execute(sync_add, a=1, b=2)


def test_circuit_breaker_validation_and_reset() -> None:
    """Verify input validation constraints and reset functionality."""
    with pytest.raises(ValueError, match="failure_threshold"):
        CircuitBreaker(failure_threshold=0)
    with pytest.raises(ValueError, match="recovery_timeout"):
        CircuitBreaker(recovery_timeout=0.0)
    with pytest.raises(ValueError, match="half_open_success_threshold"):
        CircuitBreaker(half_open_success_threshold=0)

    cb = CircuitBreaker(name="reset_test", failure_threshold=1)
    with pytest.raises(RuntimeError, match="fail"):
        cb.execute(lambda: (_ for _ in ()).throw(RuntimeError("fail")))

    assert _get_state(cb) == CircuitState.OPEN
    cb.reset()
    assert _get_state(cb) == CircuitState.CLOSED
    assert cb.consecutive_failures == 0
    assert cb.get_metrics()["total_calls"] == 0


# =============================================================================
# 2. Resilient Downstream Service Unit Tests
# =============================================================================


@pytest.mark.asyncio
async def test_resilient_payment_service_isolation() -> None:
    """Verify ResilientPaymentService prevents downstream exhaustion on repeated failures."""
    clock = MockClock()
    cb = CircuitBreaker(
        name="isolated_payment",
        failure_threshold=3,
        recovery_timeout=30.0,
        time_provider=clock,
    )
    svc = ResilientPaymentService(circuit_breaker=cb)

    # 1. Normal successful charge
    res = await svc.execute_external_charge(amount=500.0, currency="BDT", should_fail=False)
    assert res["status"] == "succeeded"
    assert res["amount"] == 500.0
    assert res["circuit_state"] == "CLOSED"
    assert svc.downstream_calls_executed == 1

    # 2. Fail 3 times to trip breaker
    for _ in range(3):
        with pytest.raises(RuntimeError):
            await svc.execute_external_charge(amount=100.0, should_fail=True)

    assert svc.downstream_calls_executed == 4
    assert cb.state == CircuitState.OPEN

    # 3. Further requests fail-fast: downstream_calls_executed MUST NOT increment!
    for _ in range(3):
        with pytest.raises(CircuitBreakerOpenException):
            await svc.execute_external_charge(amount=100.0, should_fail=False)

    assert svc.downstream_calls_executed == 4


# =============================================================================
# 3. HTTP Integration & Telemetry Endpoints
# =============================================================================


@pytest.mark.asyncio
async def test_http_circuit_breaker_charge_and_metrics_endpoints() -> None:
    """Verify HTTP API integration, 503 response envelope, Retry-After header, and metrics."""
    service = get_resilient_payment_service()
    service.reset()

    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Initial metrics: circuit is CLOSED
        metrics_res = await client.get("/metrics/circuit-breaker")
        assert metrics_res.status_code == 200
        metrics = metrics_res.json()
        assert metrics["state"] == "CLOSED"
        assert metrics["consecutive_failures"] == 0
        assert metrics["total_short_circuits"] == 0

        # 2. Execute successful charge
        charge_payload = {
            "amount": 2500.0,
            "currency": "BDT",
            "should_fail": False,
        }
        res = await client.post("/resilience/circuit-breaker/charge", json=charge_payload)
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "succeeded"
        assert data["amount"] == 2500.0
        assert data["circuit_state"] == "CLOSED"
        assert "transaction_id" in data

        # 3. Trip the breaker by triggering 5 failures
        fail_payload = {
            "amount": 500.0,
            "currency": "BDT",
            "should_fail": True,
        }
        for _attempt in range(1, 6):
            res_fail = await client.post("/resilience/circuit-breaker/charge", json=fail_payload)
            # Downstream unhandled RuntimeError is masked as HTTP 500
            assert res_fail.status_code == 500

        # 4. Breaker is now OPEN. 6th call MUST fail-fast with HTTP 503!
        res_tripped = await client.post("/resilience/circuit-breaker/charge", json=charge_payload)
        assert res_tripped.status_code == 503
        assert "Retry-After" in res_tripped.headers
        assert int(res_tripped.headers["Retry-After"]) == 30

        error_data = res_tripped.json()
        assert error_data["error"]["code"] == "CIRCUIT_BREAKER_OPEN"
        assert error_data["error"]["status_code"] == 503
        assert "Downstream service is unavailable" in error_data["error"]["message"]

        # 5. Check live metrics in OPEN state
        metrics_res = await client.get("/metrics/circuit-breaker")
        assert metrics_res.status_code == 200
        open_metrics = metrics_res.json()
        assert open_metrics["state"] == "OPEN"
        assert open_metrics["consecutive_failures"] == 5
        assert open_metrics["total_short_circuits"] >= 1
        assert open_metrics["remaining_recovery_time_seconds"] > 0.0

        # Reset service for subsequent tests
        service.reset()
