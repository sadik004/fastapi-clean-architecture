"""Metrics and rate limiter telemetry router."""

from typing import Annotated, Any
from fastapi import APIRouter, Depends, Query, Request

from app.core.dependencies import (
    check_sliding_window_rate_limit,
    get_global_rate_limiter,
)
from app.core.dsa.sliding_window import SlidingWindowLog
from app.schemas.metrics import (
    RateLimiterMetricsResponse,
    RateLimiterTestResponse,
)

router = APIRouter(prefix="/metrics", tags=["Metrics & Observability"])

# Dedicated rate limiter for integration testing (5 requests per 10 seconds)
_test_endpoint_limiter = SlidingWindowLog(window_seconds=10.0, max_requests=5)


def get_test_endpoint_limiter() -> SlidingWindowLog:
    """Return test-specific rate limiter instance for test fixtures."""
    return _test_endpoint_limiter


@router.get(
    "/rate-limiter",
    response_model=RateLimiterMetricsResponse,
    summary="Get rate limiter telemetry",
    description="Returns real-time window status, active client counts, and estimated memory footprint.",
)
async def get_rate_limiter_telemetry(
    limiter: Annotated[SlidingWindowLog, Depends(get_global_rate_limiter)],
) -> RateLimiterMetricsResponse:
    """Return real-time operational telemetry for the sliding window rate limiter."""
    metrics_data = limiter.get_metrics()
    return RateLimiterMetricsResponse(**metrics_data)


@router.get(
    "/rate-limiter/test-protected",
    response_model=RateLimiterTestResponse,
    dependencies=[
        Depends(
            check_sliding_window_rate_limit(
                window=10.0, limit=5, limiter=_test_endpoint_limiter
            )
        )
    ],
    summary="Rate-limited test endpoint",
    description="Sample endpoint protected by sliding window rate limiter (5 requests / 10s).",
)
async def rate_limited_test_endpoint(
    request: Request,
) -> RateLimiterTestResponse:
    """Protected test endpoint that verifies sliding window rate limit enforcement."""
    client_id = (
        request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
        or (request.client.host if request.client else "127.0.0.1")
    )
    count = _test_endpoint_limiter.get_client_request_count(client_id)
    return RateLimiterTestResponse(
        message="Request accepted within sliding window quota",
        client_id=client_id,
        request_number=count,
    )


@router.post(
    "/rate-limiter/evict-idle",
    summary="Evict idle clients",
    description="Manual/cron trigger to sweep and purge idle client records from memory.",
)
async def evict_idle_clients_endpoint(
    idle_seconds: Annotated[float, Query(ge=0.0)] = 60.0,
    limiter: Annotated[SlidingWindowLog, Depends(get_global_rate_limiter)] = None,  # type: ignore[assignment]
) -> dict[str, Any]:
    """Purge clients inactive for more than idle_seconds."""
    purged_count = limiter.evict_idle_clients(idle_seconds=idle_seconds)
    return {
        "purged_clients": purged_count,
        "active_clients": limiter.active_client_count(),
        "total_tracked_requests": limiter.total_tracked_requests(),
    }
