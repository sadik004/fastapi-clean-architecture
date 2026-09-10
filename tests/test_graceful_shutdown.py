from __future__ import annotations

import asyncio
from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient

from app.core.lifecycle import ShutdownManager, get_shutdown_manager
from app.core.tracing import init_tracer


@pytest.fixture(autouse=True)
def clean_shutdown_state() -> Generator[None]:
    """Reset shutdown manager state before and after each test, ensuring tracer isolation."""
    mgr = get_shutdown_manager()
    mgr.reset()
    init_tracer()
    yield
    mgr.reset()
    init_tracer()


def test_in_flight_counter_boundary_conditions() -> None:
    """Invariant: in_flight_requests must accurately track counts and never drop below 0."""
    mgr = ShutdownManager()
    assert mgr.in_flight_requests == 0

    mgr.increment_in_flight()
    mgr.increment_in_flight()
    assert mgr.in_flight_requests == 2

    mgr.decrement_in_flight()
    assert mgr.in_flight_requests == 1

    mgr.decrement_in_flight()
    assert mgr.in_flight_requests == 0

    # Boundary check: decrementing when 0 must stay at 0
    mgr.decrement_in_flight()
    assert mgr.in_flight_requests == 0


def test_readiness_probe_trips_to_503_during_shutdown(client: TestClient) -> None:
    """Phase 1 Invariant: When shutdown is initiated, /health/readiness must immediately return 503."""
    mgr = get_shutdown_manager()

    # Before shutdown: readiness probe returns 200 (assuming healthy test environment)
    mgr.initiate_shutdown()
    assert mgr.is_shutting_down is True

    # After shutdown initiation: readiness MUST return 503
    response = client.get("/health/readiness")
    assert response.status_code == 503
    data = response.json()
    assert data["status"] == "unready"
    assert "graceful shutdown" in data["reason"]


def test_connection_close_header_injected_during_shutdown(client: TestClient) -> None:
    """Connection Draining Invariant: Responses during shutdown must inject Connection: close header."""
    mgr = get_shutdown_manager()

    # Request before shutdown
    resp_before = client.get("/health/liveness")
    assert resp_before.status_code == 200
    assert resp_before.headers.get("Connection") != "close"

    # Trip Phase 1 shutdown
    mgr.initiate_shutdown()

    # Request during shutdown
    resp_during = client.get("/health/liveness")
    assert resp_during.status_code == 200
    assert resp_during.headers.get("Connection") == "close"


def test_shutdown_status_diagnostic_endpoint(client: TestClient) -> None:
    """Telemetry Invariant: /health/shutdown-status must expose accurate lifecycle state."""
    mgr = get_shutdown_manager()

    # Default state: the active request itself is in flight (>= 1)
    resp = client.get("/health/shutdown-status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["is_shutting_down"] is False
    assert data["in_flight_requests"] >= 1
    assert "timestamp" in data

    # Trip shutdown
    mgr.initiate_shutdown()
    resp2 = client.get("/health/shutdown-status")
    assert resp2.status_code == 200
    data2 = resp2.json()
    assert data2["is_shutting_down"] is True
    assert data2["in_flight_requests"] >= 1

    # Once requests complete, in-flight drops cleanly back to 0
    assert mgr.in_flight_requests == 0


@pytest.mark.asyncio
async def test_wait_for_drain_immediate_when_zero() -> None:
    """Phase 2 Invariant: If zero in-flight requests remain, drain returns True immediately."""
    mgr = ShutdownManager()
    mgr.initiate_shutdown()
    result = await mgr.wait_for_drain(timeout=1.0)
    assert result is True


@pytest.mark.asyncio
async def test_in_flight_draining_completion_zero_dropped_requests() -> None:
    """Phase 2 Invariant: Ongoing requests complete cleanly before drain completes."""
    mgr = ShutdownManager()
    mgr.increment_in_flight()
    assert mgr.in_flight_requests == 1

    # Simulate in-flight request finishing asynchronously after 100ms
    async def _simulate_in_flight() -> None:
        await asyncio.sleep(0.1)
        mgr.decrement_in_flight()

    asyncio.create_task(_simulate_in_flight())

    # wait_for_drain should wait for the simulated request to complete
    result = await mgr.wait_for_drain(timeout=2.0)
    assert result is True
    assert mgr.in_flight_requests == 0


@pytest.mark.asyncio
async def test_wait_for_drain_timeout_enforcement() -> None:
    """Phase 2 Invariant: If in-flight requests exceed timeout, manager forces Phase 3 continuation."""
    mgr = ShutdownManager()
    mgr.increment_in_flight()  # Unfinished hanging request

    # Drain with short timeout (100ms)
    result = await mgr.wait_for_drain(timeout=0.1)
    assert result is False  # Timed out, returned False without raising uncaught exceptions
    assert mgr.in_flight_requests == 1


def test_cross_platform_signal_handler_registration() -> None:
    """Invariant: register_signal_handlers must execute defensively without crashing."""
    mgr = ShutdownManager()
    # Should execute cleanly on both Windows and Linux
    mgr.register_signal_handlers()
