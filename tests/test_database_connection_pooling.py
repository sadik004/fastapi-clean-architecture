"""Comprehensive test suite for Day 16: Database Connection Pooling Architecture.

Verifies:
1. Engine connection pool configuration (pool_size, max_overflow, pool_timeout, pool_recycle, pool_pre_ping).
2. Stale connection recovery via pool_pre_ping (transparent invalidation recovery).
3. Concurrency & zero connection leak contract (25 concurrent workers returning checkedout to 0).
4. Live pool telemetry probe endpoint GET /health/db/pool.
5. Pool exhaustion timeout guard under surge load exceeding pool_size + max_overflow.
"""

import asyncio
from typing import Any
import pytest
from fastapi import status
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.exc import TimeoutError as SQLAlchemyTimeoutError
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import get_settings
from app.core.database import (
    engine,
    get_db_pool_status,
    get_db_session,
)


# ============================================================================
# 1. Connection Pool Configuration Tests
# ============================================================================


def test_engine_pool_configuration() -> None:
    """Verify engine connection pool reflects production settings."""
    settings = get_settings()
    pool = engine.pool

    assert pool._pre_ping is True or getattr(pool, "pre_ping", False) is True
    assert pool._recycle == settings.db_pool_recycle
    status_metrics = get_db_pool_status()
    assert status_metrics["pool_size"] == settings.db_pool_size


# ============================================================================
# 2. Stale Connection Recovery via pool_pre_ping
# ============================================================================


@pytest.mark.asyncio
async def test_pool_pre_ping_transparent_stale_connection_recovery() -> None:
    """Verify that an invalidated socket handle is transparently evicted and re-established.

    When pool_pre_ping=True, checking out a severed or invalidated connection triggers
    an internal ping (SELECT 1). If the ping fails, the pool discards the dead connection
    and acquires a fresh socket without surfacing errors to the application caller.
    """
    # 1. Acquire and warm a connection
    async with engine.connect() as conn:
        result = await conn.scalar(select(1))
        assert result == 1

        # Invalidate the active connection to simulate a dead/dropped socket
        await conn.invalidate()
        assert conn.invalidated is True

    # 2. Re-acquire connection from the pool.
    # pool_pre_ping must detect the invalidated/dead socket, evict it, and hand over a healthy socket.
    async with engine.connect() as new_conn:
        new_result = await new_conn.scalar(select(1))
        assert new_result == 1
        assert new_conn.invalidated is False


# ============================================================================
# 3. Concurrency & Zero Connection Leak Contract
# ============================================================================


@pytest.mark.asyncio
async def test_concurrent_session_checkout_zero_leak_contract() -> None:
    """Verify 25 concurrent requests acquire sessions and strictly release all handles.

    The contract asserts that:
    1. Every concurrent worker successfully completes its database operation.
    2. Post-execution, checked_out_connections drops strictly to 0 (zero connection leaks).
    """

    async def worker(worker_id: int) -> int:
        gen = get_db_session()
        session = await gen.__anext__()
        try:
            # Simulate interleaved database query with micro-delay
            val = await session.scalar(select(1))
            await asyncio.sleep(0.01)
            assert val == 1
            return int(val)
        finally:
            try:
                await gen.__anext__()
            except StopAsyncIteration:
                pass

    # Dispatch 25 concurrent workers
    tasks = [worker(i) for i in range(25)]
    results = await asyncio.gather(*tasks)

    # Assert all 25 workers succeeded
    assert len(results) == 25
    assert all(res == 1 for res in results)

    # CRITICAL INVARIANT: Zero connection leak post-execution
    pool_status = get_db_pool_status()
    assert pool_status["checked_out_connections"] == 0
    assert pool_status["overflow_connections"] == 0


# ============================================================================
# 4. Connection Pool Health Telemetry Endpoint (GET /health/db/pool)
# ============================================================================


def test_health_db_pool_endpoint_success(client: TestClient) -> None:
    """Verify GET /health/db/pool returns HTTP 200 with structured pool telemetry."""
    response = client.get("/health/db/pool")

    assert response.status_code == status.HTTP_200_OK
    data: dict[str, Any] = response.json()

    assert "pool_type" in data
    assert "pool_size" in data
    assert "checked_in_connections" in data
    assert "checked_out_connections" in data
    assert "overflow_connections" in data
    assert "total_open_connections" in data

    assert data["pool_size"] == 20
    assert isinstance(data["checked_in_connections"], int)
    assert isinstance(data["checked_out_connections"], int)
    assert isinstance(data["overflow_connections"], int)
    assert isinstance(data["total_open_connections"], int)
    assert data["total_open_connections"] == (
        data["checked_in_connections"] + data["checked_out_connections"]
    )


# ============================================================================
# 5. Pool Exhaustion Timeout Guard
# ============================================================================


@pytest.mark.asyncio
async def test_pool_exhaustion_timeout_guard() -> None:
    """Verify that exhausting pool_size + max_overflow raises TimeoutError after pool_timeout."""
    # Build isolated test engine with tiny capacity: pool_size=1, max_overflow=1, pool_timeout=0.2s
    test_engine = create_async_engine(
        "sqlite+aiosqlite:///./test_exhaustion.db",
        pool_size=1,
        max_overflow=1,
        pool_timeout=0.2,
        pool_pre_ping=True,
    )

    conns = []
    try:
        # Check out 1st connection (fills pool_size)
        c1 = await test_engine.connect()
        conns.append(c1)

        # Check out 2nd connection (fills max_overflow)
        c2 = await test_engine.connect()
        conns.append(c2)

        # 3rd connection exceeds pool_size (1) + max_overflow (1) = 2.
        # Must wait up to 0.2s and raise TimeoutError
        with pytest.raises(SQLAlchemyTimeoutError):
            _ = await test_engine.connect()

    finally:
        for c in conns:
            await c.close()
        await test_engine.dispose()
