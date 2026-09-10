"""Kubernetes Health Probes & Diagnostic Routing Architecture.

Exposes segregated probe contracts:
- GET /health/startup: Cold-boot initialization probe.
- GET /health/liveness: O(1) in-memory process probe (Liveness Trap prevention).
- GET /health/readiness: Timeout-bounded downstream dependency probe (PostgreSQL, Redis).
- Backward-compatible telemetry routes (/health, /health/db, /health/db/pool, /health/redis).
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, Response, status
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db_pool_status, get_db_session
from app.core.lifecycle import get_shutdown_manager
from app.core.redis import get_redis, get_redis_pool_status
from app.services.health_service import HealthCheckService, get_health_service

router = APIRouter(prefix="/health", tags=["Kubernetes Health Probes"])


@router.get(
    "/startup",
    summary="Kubernetes Startup Probe",
    description="Verifies whether application initialization routines (migrations, registries) have finished.",
    response_model=dict[str, Any],
)
async def startup_probe(
    response: Response,
    service: HealthCheckService = Depends(get_health_service),
) -> dict[str, Any]:
    """Execute Kubernetes Startup probe."""
    is_started, payload = service.check_startup()
    if not is_started:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    else:
        response.status_code = status.HTTP_200_OK
    return payload


@router.get(
    "/liveness",
    summary="Kubernetes Liveness Probe",
    description="O(1) in-memory check to verify ASGI process is alive. Never touches downstream databases to prevent the Liveness Trap.",
    response_model=dict[str, Any],
)
async def liveness_probe(
    service: HealthCheckService = Depends(get_health_service),
) -> dict[str, Any]:
    """Execute strictly O(1) in-memory Kubernetes Liveness probe."""
    return service.check_liveness()


@router.get(
    "/readiness",
    summary="Kubernetes Readiness Probe",
    description="Timeout-bounded check verifying PostgreSQL and Redis connectivity before accepting user traffic.",
    response_model=dict[str, Any],
)
async def readiness_probe(
    response: Response,
    session: AsyncSession = Depends(get_db_session),
    redis: Redis = Depends(get_redis),
    service: HealthCheckService = Depends(get_health_service),
) -> dict[str, Any]:
    """Execute Kubernetes Readiness probe."""
    shutdown_mgr = get_shutdown_manager()
    if shutdown_mgr.is_shutting_down:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {
            "status": "unready",
            "reason": "Server is draining in-flight connections for graceful shutdown",
            "in_flight_requests": shutdown_mgr.in_flight_requests,
        }

    is_ready, payload = await service.check_readiness(session=session, redis=redis)
    if not is_ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    else:
        response.status_code = status.HTTP_200_OK
    return payload


@router.get(
    "",
    summary="General Service Health",
    description="Backward-compatible top-level health probe returning service responsiveness and ISO timestamp.",
    response_model=dict[str, str],
)
@router.get(
    "/",
    include_in_schema=False,
    response_model=dict[str, str],
)
async def general_health() -> dict[str, str]:
    """Return general service status."""
    return {
        "status": "healthy",
        "timestamp": datetime.now(UTC).isoformat(),
    }


@router.get(
    "/db",
    summary="Database Connectivity Probe",
    description="Executes SELECT 1 against PostgreSQL and measures round-trip ping latency.",
    response_model=dict[str, Any],
)
async def db_health(
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """Verify database connectivity."""
    start_time = time.perf_counter()
    result = await session.scalar(select(1))
    latency_ms = round((time.perf_counter() - start_time) * 1000, 2)
    return {
        "status": "healthy",
        "database": "connected",
        "latency_ms": latency_ms,
        "scalar_result": result,
    }


@router.get(
    "/db/pool",
    summary="Database Connection Pool Telemetry",
    description="Reports pool utilization: size, checked-out connections, idle connections, and overflow.",
    response_model=dict[str, Any],
)
async def db_pool_health() -> dict[str, Any]:
    """Return connection pool status."""
    return get_db_pool_status()


@router.get(
    "/redis",
    summary="Redis Connectivity Probe",
    description="Executes async ping against Redis and measures round-trip latency.",
    response_model=dict[str, Any],
)
async def redis_health(
    redis: Redis = Depends(get_redis),
) -> dict[str, Any]:
    """Verify Redis connectivity."""
    start_time = time.perf_counter()
    await redis.ping()
    ping_ms = round((time.perf_counter() - start_time) * 1000, 2)
    return {
        "status": "healthy",
        "redis": "connected",
        "ping_ms": ping_ms,
        "pool": get_redis_pool_status(),
    }


@router.get(
    "/shutdown-status",
    summary="Shutdown & Draining Diagnostics",
    description="Exposes current server shutdown state and active in-flight request count.",
    response_model=dict[str, Any],
)
async def shutdown_status() -> dict[str, Any]:
    """Diagnostic probe for active shutdown and connection draining telemetry."""
    mgr = get_shutdown_manager()
    return {
        "is_shutting_down": mgr.is_shutting_down,
        "in_flight_requests": mgr.in_flight_requests,
        "timestamp": datetime.now(UTC).isoformat(),
    }

