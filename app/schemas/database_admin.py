"""Pydantic schemas for Database Administration, Connection Pool Telemetry, and Query Timeout Management."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class ConnectionPoolMetrics(BaseModel):
    """Real-time operational telemetry for database connection pools and timeout settings."""

    pool_type: str = Field(..., description="SQLAlchemy connection pool archetype (e.g. QueuePool, StaticPool)")
    pool_size: int = Field(..., description="Configured base connection pool capacity")
    checked_in: int = Field(..., description="Available idle connections ready in pool")
    checked_out: int = Field(..., description="Active in-use connections leased to sessions")
    overflow: int = Field(..., description="Dynamic overflow connections allocated beyond pool_size")
    total_open: int = Field(..., description="Cumulative open physical database sockets")
    statement_timeout_ms: int = Field(..., description="Hard kill limit for runaway query execution in milliseconds")
    idle_in_transaction_timeout_ms: int = Field(..., description="Idle transaction safety reaper timeout in milliseconds")
    lock_timeout_ms: int = Field(..., description="Lock acquisition safety cutoff threshold in milliseconds")
    pool_timeout_seconds: float = Field(..., description="Maximum seconds to wait before raising PoolTimeout")
    pool_recycle_seconds: int = Field(..., description="Periodic connection recycling interval in seconds")
    active_queries_count: int = Field(default=0, description="Active running statements detected in database")

    model_config = ConfigDict(from_attributes=True)


class SimulateHangingQueryRequest(BaseModel):
    """Payload to simulate long-running or hanging database queries for diagnostic verification."""

    duration_seconds: float = Field(
        default=5.0,
        ge=0.001,
        le=60.0,
        description="Duration for simulated sleep query (e.g. pg_sleep(5))",
    )
    timeout_ms: int | None = Field(
        default=None,
        ge=50,
        le=30000,
        description="Optional session-level statement timeout override in milliseconds",
    )


class SimulateHangingQueryResponse(BaseModel):
    """Diagnostic response from simulated query execution."""

    status: str = Field(..., description="Execution status outcome (e.g. completed, timed_out)")
    executed_query: str = Field(..., description="SQL query statement executed against engine")
    duration_seconds: float = Field(..., description="Requested execution duration")
    cancelled: bool = Field(..., description="Whether query was cancelled by statement timeout")
    execution_time_ms: float = Field(..., description="Measured round-trip duration in milliseconds")


class KillHangingQueriesResponse(BaseModel):
    """Result of administrative reaping of lingering long-running queries."""

    terminated_count: int = Field(..., description="Number of hanging sessions successfully terminated")
    target_max_age_seconds: int = Field(..., description="Age threshold in seconds used to identify hanging queries")
    details: list[str] = Field(default_factory=list, description="Descriptive log of reaped backend processes")
