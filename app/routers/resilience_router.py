"""Resilience router exposing Circuit Breaker charge execution and live telemetry metrics."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, status

from app.schemas.resilience import (
    CircuitBreakerChargeRequest,
    CircuitBreakerChargeResponse,
    CircuitBreakerMetricsResponse,
)
from app.services.resilient_payment_service import (
    ResilientPaymentService,
    get_resilient_payment_service,
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


__all__ = ["router"]
