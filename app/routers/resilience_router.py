"""Resilience router exposing Circuit Breaker charge execution and live telemetry metrics."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, status

from app.schemas.resilience import (
    BulkheadJobRequest,
    BulkheadJobResponse,
    BulkheadMetricsResponse,
    CircuitBreakerChargeRequest,
    CircuitBreakerChargeResponse,
    CircuitBreakerMetricsResponse,
)
from app.services.resilient_payment_service import (
    ResilientPaymentService,
    get_resilient_payment_service,
)
from app.services.resilient_report_service import (
    ResilientReportService,
    get_resilient_report_service,
)

router = APIRouter(tags=["Resilience & Circuit Breaker"])


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


__all__ = ["router"]
