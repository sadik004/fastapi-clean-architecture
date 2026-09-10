"""Comprehensive Test Suite for Day 68: Database Connection Lifecycle, Statement Timeouts & Anti-Leak Architecture.

Verifies:
1. Statement Timeout Execution & Cancellation: Queries exceeding timeout limit trigger DatabaseQueryTimeoutException.
2. Connection Anti-Leak & Recovery: After cancellation rollback, connection remains completely healthy for subsequent queries.
3. Engine Pool Pre-Ping & Invariant Verification: Engine configurations adhere to strict production constraints.
4. Connection Pool Saturation & 503 Retry-After Invariant: Pool exhaustion triggers 503 Service Unavailable with Retry-After header.
5. Administrative Telemetry & Simulation HTTP Endpoints:
   - GET /database/pool/metrics returns real-time pool stats and timeout configs.
   - POST /database/diagnostics/simulate-hanging-query enforces 504 on timeouts and completes on fast queries.
   - POST /database/diagnostics/kill-hanging executes reaper mechanics safely.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import AsyncAdaptedQueuePool

from app.core.config import settings
from app.core.database import async_session_factory, get_db_session
from app.core.exceptions import (
    ConnectionPoolExhaustedException,
    DatabaseQueryTimeoutException,
)
from app.main import app
from app.services.database_admin_service import DatabaseAdminService

# ==============================================================================
# 1. Statement Timeout & Query Cancellation Tests
# ==============================================================================


@pytest.mark.asyncio
async def test_statement_timeout_cancels_and_raises_exception() -> None:
    """Verify that a query running longer than statement timeout is aborted with DatabaseQueryTimeoutException."""
    async with async_session_factory() as session:
        # Run a 500ms sleep with a 100ms statement timeout
        with pytest.raises(DatabaseQueryTimeoutException) as exc_info:
            await DatabaseAdminService.execute_with_custom_timeout(
                session=session,
                sql_query="SELECT pg_sleep(0.5)",
                timeout_ms=100,
            )

        exc = exc_info.value
        assert exc.code == "DATABASE_QUERY_TIMEOUT"
        assert exc.timeout_ms == 100
        assert exc.query is not None
        assert "SELECT pg_sleep(0.5)" in exc.query
        assert "100ms" in exc.message


@pytest.mark.asyncio
async def test_connection_healthy_after_timeout_cancellation() -> None:
    """Verify that after a statement timeout cancellation and rollback, the connection is intact and unpoisoned."""
    async with async_session_factory() as session:
        # 1. Trigger timeout
        with pytest.raises(DatabaseQueryTimeoutException):
            await DatabaseAdminService.execute_with_custom_timeout(
                session=session,
                sql_query="SELECT pg_sleep(0.4)",
                timeout_ms=100,
            )

        # 2. Subsequent query on the same session must execute cleanly
        result = await session.execute(text("SELECT 1"))
        val = result.scalar()
        assert val == 1


# ==============================================================================
# 2. Engine Pool Configuration & Pre-Ping Verification
# ==============================================================================


def test_pool_pre_ping_and_timeout_configurations() -> None:
    """Verify production connection pool and server-side timeout configuration parameters."""
    assert settings.db_pool_pre_ping is True
    assert settings.db_statement_timeout_ms == 3000
    assert settings.db_idle_in_transaction_timeout_ms == 5000
    assert settings.db_lock_timeout_ms == 2000
    assert settings.db_pool_timeout == 5.0
    assert settings.db_pool_recycle == 1800
    assert settings.db_pool_size == 20
    assert settings.db_max_overflow == 10


# ==============================================================================
# 3. Connection Pool Saturation & HTTP 503 Retry-After Test
# ==============================================================================


def test_pool_exhaustion_maps_to_503_and_retry_after(client: TestClient) -> None:
    """Verify that pool exhaustion triggers ConnectionPoolExhaustedException with Retry-After: 5."""
    exc = ConnectionPoolExhaustedException(retry_after=5)
    assert exc.code == "CONNECTION_POOL_EXHAUSTED"
    assert exc.retry_after == 5
    assert "saturated" in exc.message.lower()

    # Test HTTP handler translation with dependency override
    async def _mock_exhausted_session() -> Any:
        raise ConnectionPoolExhaustedException(retry_after=5)

    app.dependency_overrides[get_db_session] = _mock_exhausted_session
    try:
        response = client.get("/database/pool/metrics")
        assert response.status_code == 503
        assert response.headers.get("retry-after") == "5"
        data = response.json()
        assert data["error"]["code"] == "CONNECTION_POOL_EXHAUSTED"
    finally:
        app.dependency_overrides.pop(get_db_session, None)


@pytest.mark.asyncio
async def test_simulated_pool_saturation_timeout() -> None:
    """Verify that saturating a tight connection pool raises TimeoutError, which maps to 503."""
    # Create an isolated async engine with pool_size=1, max_overflow=0, timeout=0.1s
    test_engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=AsyncAdaptedQueuePool,
        pool_size=1,
        max_overflow=0,
        pool_timeout=0.1,
    )

    try:
        # Check out the only available connection
        conn1 = await test_engine.connect()
        # Attempting to check out another connection within 0.1s should trigger TimeoutError
        with pytest.raises(Exception) as exc_info:
            await test_engine.connect()

        error_str = str(exc_info.value).lower()
        assert "queuepool limit" in error_str or "timed out" in error_str or "timeout" in error_str
        await conn1.close()
    finally:
        await test_engine.dispose()


# ==============================================================================
# 4. HTTP Endpoints: Pool Telemetry & Diagnostics
# ==============================================================================


def test_get_database_pool_metrics_endpoint(client: TestClient) -> None:
    """Verify GET /database/pool/metrics returns complete operational telemetry."""
    response = client.get("/database/pool/metrics")
    assert response.status_code == 200
    data = response.json()

    assert "pool_type" in data
    assert "pool_size" in data
    assert "checked_in" in data
    assert "checked_out" in data
    assert "overflow" in data
    assert "total_open" in data
    assert data["statement_timeout_ms"] == 3000
    assert data["idle_in_transaction_timeout_ms"] == 5000
    assert data["lock_timeout_ms"] == 2000
    assert data["pool_timeout_seconds"] == 5.0
    assert data["pool_recycle_seconds"] == 1800


def test_simulate_hanging_query_fast_success(client: TestClient) -> None:
    """Verify POST /database/diagnostics/simulate-hanging-query completes cleanly when within timeout."""
    response = client.post(
        "/database/diagnostics/simulate-hanging-query",
        json={"duration_seconds": 0.01, "timeout_ms": 1000},
    )
    assert response.status_code == 200
    data = response.json()

    assert data["status"] == "completed"
    assert data["cancelled"] is False
    assert data["duration_seconds"] == 0.01
    assert data["execution_time_ms"] > 0


def test_simulate_hanging_query_timeout_returns_504(client: TestClient) -> None:
    """Verify POST /database/diagnostics/simulate-hanging-query returns HTTP 504 when query duration exceeds timeout."""
    response = client.post(
        "/database/diagnostics/simulate-hanging-query",
        json={"duration_seconds": 0.5, "timeout_ms": 100},
    )
    assert response.status_code == 504
    data = response.json()

    # Verify structured ErrorResponse envelope
    assert "error" in data
    assert data["error"]["code"] == "DATABASE_QUERY_TIMEOUT"
    assert data["error"]["status_code"] == 504
    assert "100ms" in data["error"]["message"]


def test_kill_hanging_queries_endpoint(client: TestClient) -> None:
    """Verify POST /database/diagnostics/kill-hanging runs reaper command safely."""
    response = client.post("/database/diagnostics/kill-hanging?max_age_seconds=10")
    assert response.status_code == 200
    data = response.json()

    assert "terminated_count" in data
    assert data["target_max_age_seconds"] == 10
    assert isinstance(data["details"], list)
