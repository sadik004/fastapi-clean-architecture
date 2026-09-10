"""Pydantic v2 schemas for Resilience, Circuit Breaker & Bulkhead Telemetry."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

# =============================================================================
# Circuit Breaker Schemas
# =============================================================================


class CircuitBreakerChargeRequest(BaseModel):
    """Client request schema to execute a payment charge through the Circuit Breaker."""

    amount: float = Field(
        ...,
        gt=0.0,
        description="Charge monetary amount (must be positive)",
    )
    currency: str = Field(
        default="BDT",
        min_length=3,
        max_length=10,
        description="Three-letter currency code",
    )
    should_fail: bool = Field(
        default=False,
        description="Simulate external downstream connection failure or 500 error",
    )


class CircuitBreakerChargeResponse(BaseModel):
    """Response returned upon successful charge execution through Circuit Breaker."""

    transaction_id: str = Field(..., description="Unique generated transaction identifier")
    amount: float = Field(..., description="Charged monetary amount")
    currency: str = Field(..., description="Payment currency")
    status: str = Field(default="succeeded", description="Charge execution status")
    circuit_state: str = Field(..., description="Circuit breaker state when operation completed")
    created_at: datetime = Field(..., description="Timestamp of transaction execution")

    model_config = ConfigDict(from_attributes=True)


class CircuitBreakerMetricsResponse(BaseModel):
    """Telemetry schema exposing live Circuit Breaker state machine metrics."""

    name: str = Field(..., description="Circuit breaker instance name")
    state: str = Field(..., description="Current FSM state: CLOSED, OPEN, or HALF_OPEN")
    consecutive_failures: int = Field(..., description="Current consecutive failures counter")
    consecutive_successes: int = Field(..., description="Current consecutive successes in HALF_OPEN")
    failure_threshold: int = Field(..., description="Failures required to trip CLOSED -> OPEN")
    recovery_timeout: float = Field(..., description="Seconds in OPEN before HALF_OPEN probe")
    half_open_success_threshold: int = Field(..., description="Successes in HALF_OPEN to reset to CLOSED")
    remaining_recovery_time_seconds: float = Field(..., description="Seconds remaining before probe transition")
    total_calls: int = Field(..., description="Total execution requests initiated")
    total_successes: int = Field(..., description="Total successful executions")
    total_failures: int = Field(..., description="Total downstream failures recorded")
    total_short_circuits: int = Field(..., description="Total calls blocked by fail-fast OPEN state")

    model_config = ConfigDict(from_attributes=True)


# =============================================================================
# Bulkhead Isolation Schemas
# =============================================================================


class BulkheadJobRequest(BaseModel):
    """Request payload to dispatch a job into a bulkhead compartment."""

    report_id: str = Field(
        ...,
        min_length=3,
        max_length=100,
        description="Unique identifier for the report or export task",
    )
    duration_seconds: float = Field(
        default=0.1,
        ge=0.0,
        le=10.0,
        description="Simulated workload execution duration in seconds",
    )


class BulkheadJobResponse(BaseModel):
    """Response schema returned upon completing an isolated bulkhead job."""

    job_id: str = Field(..., description="Unique generated job execution token")
    report_id: str = Field(..., description="Originating report or query identifier")
    compartment: str = Field(..., description="Bulkhead compartment that processed the workload")
    duration_seconds: float = Field(..., description="Execution duration in seconds")
    status: str = Field(default="completed", description="Execution result status")
    completed_at: datetime = Field(..., description="Timestamp of task completion")

    model_config = ConfigDict(from_attributes=True)


class BulkheadCompartmentMetrics(BaseModel):
    """Telemetry metrics for an individual bulkhead compartment."""

    name: str = Field(..., description="Compartment name")
    max_concurrent: int = Field(..., description="Maximum simultaneous execution slots")
    max_queue: int = Field(..., description="Maximum waiting queue slots")
    active_count: int = Field(..., description="Currently active running tasks")
    waiting_count: int = Field(..., description="Tasks currently waiting in queue")
    available_slots: int = Field(..., description="Immediate available slots")
    rejections_count: int = Field(..., description="Total rejected requests due to saturation")
    total_calls: int = Field(..., description="Total calls dispatched to this compartment")
    total_successes: int = Field(..., description="Successfully completed tasks")
    total_failures: int = Field(..., description="Tasks that terminated with errors")

    model_config = ConfigDict(from_attributes=True)


class BulkheadMetricsResponse(BaseModel):
    """Aggregate telemetry response exposing all registered bulkhead compartments."""

    compartments: dict[str, BulkheadCompartmentMetrics] = Field(
        ...,
        description="Map of compartment names to live telemetry metrics",
    )

    model_config = ConfigDict(from_attributes=True)


# =============================================================================
# Exponential Backoff & Jitter Schemas
# =============================================================================


class BackoffSimulationRequest(BaseModel):
    """Payload to simulate retrying a transient failing operation with backoff."""

    target_id: str = Field(
        default="external_payment_gateway",
        min_length=2,
        max_length=100,
        description="Identifier of the target downstream service",
    )
    failures_before_success: int = Field(
        default=2,
        ge=0,
        le=10,
        description="Simulated number of failures before returning success",
    )
    max_retries: int = Field(
        default=3,
        ge=1,
        le=10,
        description="Maximum retry attempts allowed",
    )
    base_delay: float = Field(
        default=0.05,
        ge=0.001,
        le=10.0,
        description="Initial base delay in seconds",
    )
    max_delay: float = Field(
        default=1.0,
        ge=0.01,
        le=60.0,
        description="Maximum backoff ceiling cap in seconds",
    )
    strategy: str = Field(
        default="full_jitter",
        description="Jitter strategy: 'full_jitter', 'equal_jitter', 'decorrelated_jitter', or 'no_jitter'",
    )


class BackoffSimulationResponse(BaseModel):
    """Result of the backoff retry simulation."""

    target_id: str = Field(..., description="Target service identifier")
    success: bool = Field(..., description="Whether the operation ultimately succeeded")
    attempts_made: int = Field(..., description="Total execution attempts (initial + retries)")
    retries_count: int = Field(..., description="Number of retries triggered")
    delays: list[float] = Field(..., description="List of individual delay durations (seconds) between retries")
    total_delay_seconds: float = Field(..., description="Sum of sleep delays incurred across retries")
    result: dict[str, Any] = Field(..., description="Payload returned by the downstream operation")

    model_config = ConfigDict(from_attributes=True)


class BackoffDistributionResponse(BaseModel):
    """Statistical distribution of calculated jittered delays for verification."""

    attempt: int = Field(..., description="Attempt index evaluated")
    strategy: str = Field(..., description="Backoff strategy used")
    base_delay: float = Field(..., description="Base delay configured")
    max_delay: float = Field(..., description="Max delay ceiling configured")
    upper_bound: float = Field(..., description="Theoretical exponential upper bound: min(max_delay, base * 2^attempt)")
    samples: int = Field(..., description="Number of delay samples generated")
    min_delay: float = Field(..., description="Minimum observed delay among samples")
    max_calculated_delay: float = Field(..., description="Maximum observed delay among samples")
    mean_delay: float = Field(..., description="Mean/average delay across samples")
    delays: list[float] = Field(..., description="Sample of calculated delays")

    model_config = ConfigDict(from_attributes=True)

