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


class ViewsFlushResponse(BaseModel):
    """Telemetry report for Write-Behind batch synchronization to database."""

    model_config = ConfigDict(extra="forbid")

    flushed_records: int = Field(..., description="Number of unique user entities whose counts were updated")
    total_views: int = Field(..., description="Cumulative count of views flushed to the persistent store")
    status: str = Field(..., description="Execution status ('success', 'no_pending_data', or error)")


class UserViewResponse(BaseModel):
    """Acknowledgment payload for Write-Behind profile view registration."""

    model_config = ConfigDict(extra="forbid")

    user_id: int = Field(..., description="ID of the user whose profile was viewed")
    status: str = Field(..., description="Status of the write operation")
    mode: str = Field(..., description="Storage ingestion mode ('write-behind' or fallback)")


class UserViewsSummaryResponse(BaseModel):
    """Summary of persistent database views, active Redis pending views, and combined total."""

    model_config = ConfigDict(extra="forbid")

    user_id: int = Field(..., description="Target user identifier")
    persistent_views: int = Field(..., description="Views flushed and persisted to relational storage")
    pending_views: int = Field(..., description="Unflushed views currently buffered in-memory in Redis")
    total_views: int = Field(..., description="Total real-time view count (persistent + pending)")


class XFetchMetricsResponse(BaseModel):
    """Operational telemetry report for XFetch Cache Stampede defense."""

    model_config = ConfigDict(extra="forbid")

    normal_hits: int = Field(..., description="Requests served warm data from cache without recomputation")
    early_recomputations: int = Field(
        ..., description="Requests probabilistically selected to refresh cache before expiry"
    )
    hard_misses: int = Field(..., description="Initial requests on cold cache key requiring immediate computation")
    total_requests: int = Field(..., description="Cumulative total requests handled by XFetch")


class BloomFilterMetricsResponse(BaseModel):
    """Operational telemetry report for Bloom Filter cache penetration shield."""

    model_config = ConfigDict(extra="forbid")

    capacity: int = Field(..., description="Configured maximum item capacity limit (N)")
    bit_size: int = Field(..., description="Total size of the bit array in bits (m)")
    bit_size_kb: float = Field(..., description="Memory footprint of the bit array in kilobytes")
    hash_count: int = Field(..., description="Number of independent hash functions (k)")
    item_count: int = Field(..., description="Total items currently tracked in the Bloom Filter")
    false_positive_probability: float = Field(..., description="Current theoretical false positive probability P")


class BloomFilterCheckResponse(BaseModel):
    """Result of testing an ID against the Bloom Filter penetration shield."""

    model_config = ConfigDict(extra="forbid")

    user_id: int = Field(..., description="User ID tested against Bloom Filter")
    probably_exists: bool = Field(..., description="True if probably in DB; False if guaranteed 100% not in DB")
    db_queried: bool = Field(
        default=False,
        description="Whether database repository was queried (strictly False when rejected)",
    )
    status: str = Field(..., description="Status summary of the penetration shield")
