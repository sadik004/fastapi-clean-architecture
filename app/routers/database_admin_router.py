"""Database Administration & Telemetry Router.

Exposes endpoints for connection pool telemetry, statement timeout diagnostics,
and runaway query mitigation.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db_session
from app.schemas.database_admin import (
    ConnectionPoolMetrics,
    KillHangingQueriesResponse,
    SimulateHangingQueryRequest,
    SimulateHangingQueryResponse,
)
from app.services.database_admin_service import DatabaseAdminService

router = APIRouter(
    prefix="/database",
    tags=["Database Administration & Lifecycle"],
)


@router.get(
    "/pool/metrics",
    response_model=ConnectionPoolMetrics,
    status_code=status.HTTP_200_OK,
    summary="Get Database Connection Pool Telemetry",
    description=(
        "Returns real-time connection pool statistics (checked in, checked out, "
        "overflow, total open) along with configured server-side timeouts "
        "(statement_timeout, idle_in_transaction_session_timeout, lock_timeout)."
    ),
)
async def get_pool_metrics(
    session: AsyncSession = Depends(get_db_session),
) -> ConnectionPoolMetrics:
    """Retrieve operational metrics from the active database connection pool."""
    return await DatabaseAdminService.get_connection_pool_metrics(session=session)


@router.post(
    "/diagnostics/simulate-hanging-query",
    response_model=SimulateHangingQueryResponse,
    status_code=status.HTTP_200_OK,
    summary="Simulate Slow/Hanging Query",
    description=(
        "Executes a simulated long-running sleep statement (e.g. pg_sleep(N)) "
        "to test statement_timeout enforcement. If query duration exceeds statement_timeout, "
        "the database cancels execution, rolls back transaction, and returns HTTP 504 Gateway Timeout."
    ),
)
async def simulate_hanging_query(
    payload: SimulateHangingQueryRequest,
    session: AsyncSession = Depends(get_db_session),
) -> SimulateHangingQueryResponse:
    """Simulate a hanging query and verify cancellation cutoff."""
    return await DatabaseAdminService.simulate_hanging_query(
        session=session,
        duration_seconds=payload.duration_seconds,
        timeout_ms=payload.timeout_ms,
    )


@router.post(
    "/diagnostics/kill-hanging",
    response_model=KillHangingQueriesResponse,
    status_code=status.HTTP_200_OK,
    summary="Reap Lingering Long-Running Queries",
    description=(
        "Administrative endpoint to terminate runaway active queries exceeding "
        "the specified age threshold via pg_terminate_backend()."
    ),
)
async def kill_hanging_queries(
    max_age_seconds: int = Query(
        default=10,
        ge=1,
        le=3600,
        description="Threshold age in seconds to target for query termination",
    ),
    session: AsyncSession = Depends(get_db_session),
) -> KillHangingQueriesResponse:
    """Terminate lingering uncommitted or hanging database backends."""
    return await DatabaseAdminService.kill_hanging_queries(
        session=session,
        max_age_seconds=max_age_seconds,
    )
