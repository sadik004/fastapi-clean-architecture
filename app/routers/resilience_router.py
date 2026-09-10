"""Resilience router exposing Circuit Breaker charge execution and live telemetry metrics."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response, status

from app.core.resilience.backoff import calculate_backoff
from app.core.resilience.fallback import DegradationLevel
from app.schemas.resilience import (
    BackoffDistributionResponse,
    BackoffSimulationRequest,
    BackoffSimulationResponse,
    BulkheadJobRequest,
    BulkheadJobResponse,
    BulkheadMetricsResponse,
    CircuitBreakerChargeRequest,
    CircuitBreakerChargeResponse,
    CircuitBreakerMetricsResponse,
    RecommendationResponse,
    SeedCacheRequest,
    SeedCacheResponse,
)
from app.services.recommendation_service import (
    ProductRecommendationService,
    get_recommendation_service,
)
from app.services.resilient_payment_service import (
    ResilientPaymentService,
    get_resilient_payment_service,
)
from app.services.resilient_report_service import (
    ResilientReportService,
    get_resilient_report_service,
)
from app.services.resilient_third_party_service import (
    ResilientThirdPartyService,
    get_resilient_third_party_service,
)

router = APIRouter(tags=["Resilience, Circuit Breaker, Backoff & Fallback"])


@router.post(
    "/resilience/circuit-breaker/charge",
    response_model=CircuitBreakerChargeResponse,
    status_code=status.HTTP_200_OK,
    summary="Execute payment charge through Circuit Breaker resilience engine",
    description=(
        "Executes payment charge wrapped in Circuit Breaker state machine. "
        "Fails-fast with HTTP 503 (CircuitBreakerOpenException) and 'Retry-After' header "
        "if downstream failure threshold has been reached."
    ),
)
async def execute_circuit_breaker_charge_endpoint(
    payload: CircuitBreakerChargeRequest,
    payment_service: Annotated[ResilientPaymentService, Depends(get_resilient_payment_service)],
) -> CircuitBreakerChargeResponse:
    """Execute payment charge with O(1) circuit breaker failure isolation."""
    result = await payment_service.execute_external_charge(
        amount=payload.amount,
        currency=payload.currency,
        should_fail=payload.should_fail,
    )
    return CircuitBreakerChargeResponse(**result)


@router.get(
    "/metrics/circuit-breaker",
    response_model=CircuitBreakerMetricsResponse,
    status_code=status.HTTP_200_OK,
    summary="Retrieve live telemetry and state machine status for Circuit Breaker",
)
@router.get(
    "/resilience/circuit-breaker/metrics",
    response_model=CircuitBreakerMetricsResponse,
    status_code=status.HTTP_200_OK,
    include_in_schema=False,
)
async def get_circuit_breaker_metrics_endpoint(
    payment_service: Annotated[ResilientPaymentService, Depends(get_resilient_payment_service)],
) -> CircuitBreakerMetricsResponse:
    """Return live FSM state (CLOSED, OPEN, HALF_OPEN), failure count, and remaining recovery window."""
    metrics = payment_service.circuit_breaker.get_metrics()
    return CircuitBreakerMetricsResponse(**metrics)


@router.post(
    "/resilience/bulkhead/heavy-job",
    response_model=BulkheadJobResponse,
    status_code=status.HTTP_200_OK,
    summary="Execute heavy batch reporting job with strict bulkhead concurrency clamping",
    description=(
        "Executes a heavy resource-intensive report job isolated within a 2-slot Bulkhead. "
        "Throws HTTP 503 (BulkheadFullException) with Retry-After header immediately "
        "when concurrent capacity is saturated."
    ),
)
async def execute_bulkhead_heavy_job_endpoint(
    payload: BulkheadJobRequest,
    report_service: Annotated[ResilientReportService, Depends(get_resilient_report_service)],
) -> BulkheadJobResponse:
    """Execute heavy report job clamped by Bulkhead isolation."""
    result = await report_service.generate_heavy_report(
        report_id=payload.report_id,
        duration_seconds=payload.duration_seconds,
    )
    return BulkheadJobResponse(**result)


@router.get(
    "/resilience/bulkhead/critical-status",
    response_model=BulkheadJobResponse,
    status_code=status.HTTP_200_OK,
    summary="Retrieve high-priority critical system status isolated in separate bulkhead",
    description=(
        "Executes lightweight critical operational checks under a dedicated 20-slot Bulkhead, "
        "guaranteed to succeed even if heavy reporting is completely saturated."
    ),
)
async def get_bulkhead_critical_status_endpoint(
    report_service: Annotated[ResilientReportService, Depends(get_resilient_report_service)],
) -> BulkheadJobResponse:
    """Retrieve critical health/readiness status immune to heavy report resource starvation."""
    result = await report_service.get_critical_status()
    return BulkheadJobResponse(**result)


@router.get(
    "/metrics/bulkhead",
    response_model=BulkheadMetricsResponse,
    status_code=status.HTTP_200_OK,
    summary="Retrieve live bulkhead compartment telemetry and concurrency metrics",
)
@router.get(
    "/resilience/bulkhead/metrics",
    response_model=BulkheadMetricsResponse,
    status_code=status.HTTP_200_OK,
    include_in_schema=False,
)
async def get_bulkhead_metrics_endpoint(
    report_service: Annotated[ResilientReportService, Depends(get_resilient_report_service)],
) -> BulkheadMetricsResponse:
    """Return live active, waiting, and rejected metrics for all bulkhead compartments."""
    metrics = report_service.get_metrics()
    return BulkheadMetricsResponse(**metrics)


@router.post(
    "/resilience/backoff/simulate-retry",
    response_model=BackoffSimulationResponse,
    status_code=status.HTTP_200_OK,
    summary="Simulate transient downstream call with Exponential Backoff and Jitter",
    description=(
        "Simulates a downstream failure sequence recovering via randomized jittered retries, "
        "demonstrating autonomous recovery and traffic stampede elimination."
    ),
)
async def simulate_backoff_retry_endpoint(
    payload: BackoffSimulationRequest,
    service: Annotated[ResilientThirdPartyService, Depends(get_resilient_third_party_service)],
) -> BackoffSimulationResponse:
    """Execute simulated external call with customizable backoff and jitter strategy."""
    result = await service.execute_simulated_call(
        target_id=payload.target_id,
        failures_before_success=payload.failures_before_success,
        max_retries=payload.max_retries,
        base_delay=payload.base_delay,
        max_delay=payload.max_delay,
        strategy=payload.strategy,
    )
    return BackoffSimulationResponse(**result)


@router.get(
    "/resilience/backoff/distribution",
    response_model=BackoffDistributionResponse,
    status_code=status.HTTP_200_OK,
    summary="Generate statistical delay sample distribution for a specific retry attempt",
    description=(
        "Computes N delay samples using the requested backoff/jitter strategy, "
        "verifying randomization bounds and variance."
    ),
)
async def get_backoff_distribution_endpoint(
    attempt: Annotated[int, Query(ge=0, le=30, description="Retry attempt index")] = 3,
    base_delay: Annotated[float, Query(ge=0.001, le=10.0, description="Base delay seconds")] = 0.1,
    max_delay: Annotated[float, Query(ge=0.01, le=60.0, description="Max delay ceiling seconds")] = 5.0,
    strategy: Annotated[str, Query(description="Strategy: full_jitter, equal_jitter, decorrelated_jitter, no_jitter")] = "full_jitter",
    samples: Annotated[int, Query(ge=1, le=1000, description="Sample count")] = 100,
) -> BackoffDistributionResponse:
    """Return statistical distribution of calculated delays for verification."""
    delays: list[float] = []
    prev_delay: float | None = None

    for _ in range(samples):
        delay = calculate_backoff(
            attempt=attempt,
            base_delay=base_delay,
            max_delay=max_delay,
            strategy=strategy,
            previous_delay=prev_delay,
        )
        delays.append(round(delay, 6))
        prev_delay = delay

    safe_base = max(0.0001, base_delay)
    safe_max = max(safe_base, max_delay)
    upper_bound = min(safe_max, safe_base * (2 ** min(attempt, 30)))

    min_delay = min(delays)
    max_calculated_delay = max(delays)
    mean_delay = sum(delays) / len(delays)

    return BackoffDistributionResponse(
        attempt=attempt,
        strategy=strategy,
        base_delay=base_delay,
        max_delay=max_delay,
        upper_bound=round(upper_bound, 6),
        samples=samples,
        min_delay=min_delay,
        max_calculated_delay=max_calculated_delay,
        mean_delay=round(mean_delay, 6),
        delays=delays,
    )


@router.get(
    "/resilience/recommendations/{user_id}",
    response_model=RecommendationResponse,
    status_code=status.HTTP_200_OK,
    summary="Fetch personalized product recommendations with multi-tier graceful degradation",
    description=(
        "Returns personalized recommendations through a 3-tier fallback safety ladder: "
        "Primary AI Service -> Stale Redis Cache -> Static Trending Default. "
        "Injects HTTP telemetry headers X-Degraded-Mode and X-Degradation-Level, guaranteeing zero 500 errors."
    ),
)
async def get_personalized_recommendations_endpoint(
    user_id: int,
    response: Response,
    service: Annotated[ProductRecommendationService, Depends(get_recommendation_service)],
    simulate_failure: Annotated[bool, Query(description="Simulate external AI service outage")] = False,
) -> RecommendationResponse:
    """Execute recommendation query protected by multi-tier FallbackEngine."""
    result, level = await service.get_personalized_recommendations(
        user_id=user_id,
        simulate_failure=simulate_failure,
    )

    response.headers["X-Degraded-Mode"] = "FALSE" if level == DegradationLevel.PRIMARY else "TRUE"
    response.headers["X-Degradation-Level"] = level.value

    return RecommendationResponse(**result)


@router.post(
    "/resilience/recommendations/seed-cache",
    response_model=SeedCacheResponse,
    status_code=status.HTTP_200_OK,
    summary="Prime fallback Redis cache for recommendation graceful degradation testing",
)
async def seed_recommendations_cache_endpoint(
    payload: SeedCacheRequest,
    service: Annotated[ProductRecommendationService, Depends(get_recommendation_service)],
) -> SeedCacheResponse:
    """Seed user recommendation cache with predetermined items."""
    raw_items = [item.model_dump() for item in payload.items]
    cache_key = await service.seed_user_cache(
        user_id=payload.user_id,
        items=raw_items,
        ttl_seconds=payload.ttl_seconds,
    )
    return SeedCacheResponse(
        user_id=payload.user_id,
        items_count=len(payload.items),
        cache_key=cache_key,
        ttl_seconds=payload.ttl_seconds,
        status="seeded",
    )


__all__ = ["router"]
