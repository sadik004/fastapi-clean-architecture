"""Metrics and rate limiter telemetry router."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request
from redis.asyncio import Redis

from app.core.dependencies import (
    RateLimitGuard,
    check_sliding_window_rate_limit,
    get_analytics_service,
    get_global_rate_limiter,
    get_user_bloom_filter,
)
from app.core.dsa.bloom_filter import BloomFilter
from app.core.dsa.sliding_window import SlidingWindowLog
from app.core.redis import get_redis
from app.schemas.metrics import (
    BloomFilterCheckResponse,
    BloomFilterMetricsResponse,
    CacheMetricsResponse,
    RateLimiterMetricsResponse,
    RateLimiterTestResponse,
    ViewsFlushResponse,
    XFetchMetricsResponse,
)
from app.services.analytics_service import AnalyticsService
from app.services.cache_service import CacheService, get_cache_service
from app.services.rate_limiter_service import RateLimiterService

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
    "/cache",
    response_model=CacheMetricsResponse,
    summary="Get cache telemetry",
    description="Returns real-time cache hits, misses, and calculated hit ratio for Redis Cache-Aside.",
)
async def get_cache_telemetry(
    cache_service: Annotated[CacheService, Depends(get_cache_service)],
) -> CacheMetricsResponse:
    """Return real-time operational telemetry for Redis cache hit ratio."""
    metrics_data = await cache_service.get_metrics()
    return CacheMetricsResponse(**metrics_data)


@router.get(
    "/xfetch",
    response_model=XFetchMetricsResponse,
    summary="Get XFetch telemetry",
    description="Returns real-time telemetry on normal hits, early recomputations, and hard misses for XFetch cache stampede defense.",
)
async def get_xfetch_telemetry(
    cache_service: Annotated[CacheService, Depends(get_cache_service)],
) -> XFetchMetricsResponse:
    """Return real-time operational telemetry for XFetch stampede defense."""
    metrics_data = await cache_service.get_xfetch_metrics()
    return XFetchMetricsResponse(**metrics_data)


@router.get(
    "/rate-limiter/test-protected",
    response_model=RateLimiterTestResponse,
    dependencies=[Depends(check_sliding_window_rate_limit(window=10.0, limit=5, limiter=_test_endpoint_limiter))],
    summary="Rate-limited test endpoint",
    description="Sample endpoint protected by sliding window rate limiter (5 requests / 10s).",
)
async def rate_limited_test_endpoint(
    request: Request,
) -> RateLimiterTestResponse:
    """Protected test endpoint that verifies sliding window rate limit enforcement."""
    client_id = request.headers.get("X-Forwarded-For", "").split(",")[0].strip() or (
        request.client.host if request.client else "127.0.0.1"
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


@router.post(
    "/views/flush",
    response_model=ViewsFlushResponse,
    summary="Flush pending profile views to database",
    description="Synchronizes accumulated views from Redis Write-Behind buffer to database using atomic pipeline.",
)
async def flush_pending_views_endpoint(
    analytics_service: Annotated[AnalyticsService, Depends(get_analytics_service)],
) -> ViewsFlushResponse:
    """Trigger atomic batch flush of write-behind pending profile views to repository."""
    result = await analytics_service.sync_pending_views_to_db()
    return ViewsFlushResponse(**result)


@router.get(
    "/bloom-filter",
    response_model=BloomFilterMetricsResponse,
    summary="Get Bloom Filter telemetry",
    description="Returns capacity, bit array size in bits and KB, hash count, item count, and theoretical false positive probability.",
)
async def get_bloom_filter_telemetry(
    bloom_filter: Annotated[BloomFilter, Depends(get_user_bloom_filter)],
) -> BloomFilterMetricsResponse:
    """Return real-time operational telemetry for the user ID Bloom Filter."""
    metrics_data = bloom_filter.get_metrics()
    return BloomFilterMetricsResponse(**metrics_data)


@router.get(
    "/bloom-filter/check/{user_id}",
    response_model=BloomFilterCheckResponse,
    summary="Probabilistic check of User ID membership",
    description="Tests whether a user ID passes the Bloom Filter shield without querying database or Redis cache.",
)
async def check_user_bloom_filter(
    user_id: int,
    bloom_filter: Annotated[BloomFilter, Depends(get_user_bloom_filter)],
) -> BloomFilterCheckResponse:
    """Check whether a user ID exists in the Bloom filter without touching DB or cache."""
    exists = bloom_filter.contains(user_id)
    return BloomFilterCheckResponse(
        user_id=user_id,
        probably_exists=exists,
        db_queried=False,
        status="PASSED_FILTER_PROCEED_TO_CACHE" if exists else "BLOCKED_BY_FILTER_ZERO_DB_QUERY",
    )


@router.get(
    "/rate-limit/{client_id}",
    summary="Get distributed rate limit status for client",
    description="Inspects real-time sliding window active requests and TTL in Redis for a given client identifier.",
)
async def get_distributed_rate_limit_status(
    client_id: str,
    redis: Annotated[Redis, Depends(get_redis)],
    window_seconds: Annotated[float, Query(ge=1.0)] = 60.0,
) -> dict[str, Any]:
    """Retrieve real-time active requests and TTL for a client in Redis."""
    service = RateLimiterService(redis_client=redis)
    return await service.get_client_status(key=client_id, window_seconds=window_seconds)


@router.get(
    "/test-rate-limit/distributed",
    dependencies=[Depends(RateLimitGuard(limit=5, window_seconds=10.0, scope="test"))],
    summary="Protected test endpoint for distributed sliding window rate limiter",
    description="Endpoint limited to 5 requests per 10 seconds via Redis ZSET sliding window.",
)
async def distributed_rate_limit_test_metrics_endpoint(
    request: Request,
) -> dict[str, Any]:
    """Protected test route under /metrics prefix."""
    return {
        "message": "Request accepted within distributed sliding window quota",
        "client_id": getattr(request.state, "rate_limit_client", "unknown"),
        "request_number": getattr(request.state, "rate_limit_count", 1),
    }
