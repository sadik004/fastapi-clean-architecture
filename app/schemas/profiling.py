"""Pydantic DTO Schemas for Performance Profiling & Headless Load Testing Telemetry."""

from __future__ import annotations

from pydantic import BaseModel, Field


class PercentileStatsResponse(BaseModel):
    """SLA latency percentile distribution in milliseconds."""

    p50: float = Field(description="50th percentile (Median) latency in ms")
    p90: float = Field(description="90th percentile latency in ms")
    p95: float = Field(description="95th percentile latency in ms")
    p99: float = Field(description="99th percentile latency in ms")
    min_latency_ms: float = Field(description="Minimum measured request latency")
    max_latency_ms: float = Field(description="Maximum measured request latency")
    mean_latency_ms: float = Field(description="Arithmetic mean latency")


class BenchmarkResultResponse(BaseModel):
    """Result summary for an individual benchmark scenario."""

    endpoint: str = Field(description="Target endpoint URI path")
    method: str = Field(description="HTTP method (GET, POST, etc.)")
    concurrency: int = Field(description="Simultaneous concurrent worker count")
    total_requests: int = Field(description="Total requests dispatched")
    successful_requests: int = Field(description="Count of HTTP 2xx/3xx responses")
    failed_requests: int = Field(description="Count of error responses or timeouts")
    error_rate_pct: float = Field(description="Error percentage (failed / total)")
    duration_seconds: float = Field(description="Elapsed test duration in seconds")
    requests_per_second: float = Field(description="Throughput in requests per second (RPS)")
    latency_stats: PercentileStatsResponse = Field(description="Latency distribution metrics")
    sla_passed: bool = Field(description="True if P95 <= 100ms and error rate < 0.1%")


class BenchmarkReportResponse(BaseModel):
    """Aggregated load test suite report."""

    timestamp: str = Field(description="ISO-8601 UTC timestamp of execution")
    overall_status: str = Field(description="PASS if all scenarios met SLA, else FAIL")
    total_requests: int = Field(description="Total requests processed across all scenarios")
    total_duration_seconds: float = Field(description="Total elapsed duration in seconds")
    average_rps: float = Field(description="Aggregate requests per second throughput")
    scenarios: list[BenchmarkResultResponse] = Field(description="Itemized scenario results")
