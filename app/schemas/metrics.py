"""Pydantic schemas for operational telemetry and rate limiter metrics."""

from pydantic import BaseModel, ConfigDict, Field


class RateLimiterMetricsResponse(BaseModel):
    """Real-time operational telemetry for the Sliding Window Log rate limiter."""

    model_config = ConfigDict(extra="forbid")

    window_seconds: float = Field(..., description="Duration of the rolling window in seconds")
    max_requests: int = Field(..., description="Maximum allowed requests within the window")
    active_clients: int = Field(..., description="Number of currently tracked unique clients")
    total_tracked_requests: int = Field(..., description="Total active request timestamps across all clients")
    estimated_memory_bytes: int = Field(..., description="Approximate in-memory footprint in bytes")
    timestamp: float = Field(..., description="Current system timestamp in Unix epoch seconds")


class RateLimiterTestResponse(BaseModel):
    """Response payload for testing rate-limited endpoints."""

    model_config = ConfigDict(extra="forbid")

    message: str = Field(..., description="Confirmation message")
    client_id: str = Field(..., description="Detected client identifier")
    request_number: int = Field(..., description="Ordinal request count in current window")


class CacheMetricsResponse(BaseModel):
    """Operational telemetry for Redis Cache-Aside performance."""

    model_config = ConfigDict(extra="forbid")

    hits: int = Field(..., description="Total count of cache hits")
    misses: int = Field(..., description="Total count of cache misses")
    hit_ratio: float = Field(..., description="Ratio of hits to total cache requests (hits / (hits + misses))")
