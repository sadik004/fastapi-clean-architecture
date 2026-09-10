"""Headless Performance Profiling & Benchmark Service.

Provides asynchronous in-process and headless load generation, concurrency scaling,
statistical SLA percentile calculation (P50, P90, P95, P99), and Little's Law throughput analysis.
"""

from __future__ import annotations

import asyncio
import math
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any

import httpx
from starlette.types import ASGIApp

from app.core.logging import get_logger

logger = get_logger("app.profiling")


@dataclass(slots=True, frozen=True)
class PercentileStats:
    """Statistical percentile and latency distribution summary (in milliseconds)."""

    p50: float
    p90: float
    p95: float
    p99: float
    min_latency_ms: float
    max_latency_ms: float
    mean_latency_ms: float

    def to_dict(self) -> dict[str, float]:
        """Serialize stats to dictionary representation."""
        return asdict(self)


@dataclass(slots=True, frozen=True)
class BenchmarkResult:
    """Benchmark outcome for a specific endpoint and concurrency configuration."""

    endpoint: str
    method: str
    concurrency: int
    total_requests: int
    successful_requests: int
    failed_requests: int
    error_rate_pct: float
    duration_seconds: float
    requests_per_second: float
    latency_stats: PercentileStats
    sla_passed: bool

    def to_dict(self) -> dict[str, Any]:
        """Serialize result to dictionary representation."""
        return {
            "endpoint": self.endpoint,
            "method": self.method,
            "concurrency": self.concurrency,
            "total_requests": self.total_requests,
            "successful_requests": self.successful_requests,
            "failed_requests": self.failed_requests,
            "error_rate_pct": self.error_rate_pct,
            "duration_seconds": self.duration_seconds,
            "requests_per_second": self.requests_per_second,
            "latency_stats": self.latency_stats.to_dict(),
            "sla_passed": self.sla_passed,
        }


@dataclass(slots=True, frozen=True)
class BenchmarkReport:
    """Aggregated load test suite report across benchmarked endpoints."""

    timestamp: str
    overall_status: str  # "PASS" | "FAIL"
    total_requests: int
    total_duration_seconds: float
    average_rps: float
    scenarios: list[BenchmarkResult]

    def to_dict(self) -> dict[str, Any]:
        """Serialize report to JSON-compatible dictionary."""
        return {
            "timestamp": self.timestamp,
            "overall_status": self.overall_status,
            "total_requests": self.total_requests,
            "total_duration_seconds": self.total_duration_seconds,
            "average_rps": self.average_rps,
            "scenarios": [s.to_dict() for s in self.scenarios],
        }


def calculate_percentiles(latencies_ms: list[float]) -> PercentileStats:
    """Calculate statistical SLA percentiles (P50, P90, P95, P99) in O(N log N) time.

    Uses linear interpolation between adjacent ranks, matching numpy and standard monitoring systems.
    """
    if not latencies_ms:
        return PercentileStats(
            p50=0.0,
            p90=0.0,
            p95=0.0,
            p99=0.0,
            min_latency_ms=0.0,
            max_latency_ms=0.0,
            mean_latency_ms=0.0,
        )

    sorted_latencies = sorted(latencies_ms)
    n = len(sorted_latencies)

    def _interpolate(percentile: float) -> float:
        if n == 1:
            return sorted_latencies[0]
        rank = (percentile / 100.0) * (n - 1)
        lower_idx = math.floor(rank)
        upper_idx = math.ceil(rank)
        fraction = rank - lower_idx
        return sorted_latencies[lower_idx] + fraction * (sorted_latencies[upper_idx] - sorted_latencies[lower_idx])

    p50 = round(_interpolate(50.0), 3)
    p90 = round(_interpolate(90.0), 3)
    p95 = round(_interpolate(95.0), 3)
    p99 = round(_interpolate(99.0), 3)
    min_val = round(sorted_latencies[0], 3)
    max_val = round(sorted_latencies[-1], 3)
    mean_val = round(sum(sorted_latencies) / n, 3)

    return PercentileStats(
        p50=p50,
        p90=p90,
        p95=p95,
        p99=p99,
        min_latency_ms=min_val,
        max_latency_ms=max_val,
        mean_latency_ms=mean_val,
    )


class ProfilingService:
    """Orchestrates headless load tests, concurrency ramp-ups, and SLA benchmarking."""

    def __init__(self, app: ASGIApp | None = None) -> None:
        self.app: ASGIApp | None = app
        self._latest_report: BenchmarkReport | None = None

    def get_latest_report(self) -> BenchmarkReport | None:
        """Retrieve the most recent benchmark report or None."""
        return self._latest_report

    async def run_scenario(
        self,
        endpoint: str,
        method: str = "GET",
        concurrency: int = 25,
        total_requests: int = 250,
        headers: dict[str, str] | None = None,
        json_payload: dict[str, Any] | None = None,
        timeout_seconds: float = 10.0,
        target_app: ASGIApp | None = None,
        base_url: str = "http://testserver",
        max_p95_ms: float = 100.0,
    ) -> BenchmarkResult:
        """Execute concurrent load against a specific endpoint using asyncio semaphores."""
        app_to_test = target_app or self.app
        if app_to_test is None:
            raise ValueError("No ASGIApp provided to ProfilingService for in-process execution.")

        effective_concurrency = max(1, min(concurrency, total_requests))
        semaphore = asyncio.Semaphore(effective_concurrency)
        latencies_ms: list[float] = []
        successful_requests = 0
        failed_requests = 0

        transport = httpx.ASGITransport(app=app_to_test)
        req_headers = headers or {}

        async with httpx.AsyncClient(transport=transport, base_url=base_url, timeout=timeout_seconds) as client:
            request_counter = 0
            counter_lock = asyncio.Lock()

            async def _worker() -> None:
                nonlocal successful_requests, failed_requests
                while True:
                    async with counter_lock:
                        nonlocal request_counter
                        if request_counter >= total_requests:
                            break
                        request_counter += 1

                    async with semaphore:
                        t_start = time.perf_counter()
                        try:
                            if method.upper() == "GET":
                                response = await client.get(endpoint, headers=req_headers)
                            elif method.upper() == "POST":
                                response = await client.post(endpoint, json=json_payload, headers=req_headers)
                            else:
                                response = await client.request(method.upper(), endpoint, headers=req_headers)

                            latency = (time.perf_counter() - t_start) * 1000.0
                            latencies_ms.append(latency)

                            if 200 <= response.status_code < 400:
                                successful_requests += 1
                            else:
                                failed_requests += 1
                        except Exception as exc:
                            latency = (time.perf_counter() - t_start) * 1000.0
                            latencies_ms.append(latency)
                            failed_requests += 1
                            logger.warning("benchmark_request_error", endpoint=endpoint, error=str(exc))

            start_wall = time.perf_counter()
            workers = [asyncio.create_task(_worker()) for _ in range(effective_concurrency)]
            await asyncio.gather(*workers)
            total_duration = max(time.perf_counter() - start_wall, 0.0001)

        error_rate = round((failed_requests / total_requests) * 100.0, 2)
        rps = round(total_requests / total_duration, 2)
        latency_stats = calculate_percentiles(latencies_ms)

        # Enforce SLA: P95 <= max_p95_ms and Error Rate < 0.1%
        sla_passed = (latency_stats.p95 <= max_p95_ms) and (error_rate < 0.1)

        return BenchmarkResult(
            endpoint=endpoint,
            method=method.upper(),
            concurrency=effective_concurrency,
            total_requests=total_requests,
            successful_requests=successful_requests,
            failed_requests=failed_requests,
            error_rate_pct=error_rate,
            duration_seconds=round(total_duration, 3),
            requests_per_second=rps,
            latency_stats=latency_stats,
            sla_passed=sla_passed,
        )

    async def run_full_profile_suite(
        self,
        target_app: ASGIApp | None = None,
        concurrency: int = 25,
        requests_per_scenario: int = 150,
    ) -> BenchmarkReport:
        """Run standard multi-scenario benchmark suite and record latest report."""
        app_instance = target_app or self.app
        scenarios: list[BenchmarkResult] = []

        start_time = time.perf_counter()

        # Warm up ASGI routing and database connection pool
        await self.run_scenario(
            endpoint="/catalog/items/keyset?limit=20",
            method="GET",
            concurrency=1,
            total_requests=1,
            target_app=app_instance,
        )

        # Scenario 1: Catalog Keyset Pagination
        res1 = await self.run_scenario(
            endpoint="/catalog/items/keyset?limit=20",
            method="GET",
            concurrency=concurrency,
            total_requests=requests_per_scenario,
            target_app=app_instance,
            max_p95_ms=150.0,
        )
        scenarios.append(res1)

        # Scenario 2: Health Liveness Probe (Zero-I/O)
        res2 = await self.run_scenario(
            endpoint="/health/liveness",
            method="GET",
            concurrency=concurrency,
            total_requests=requests_per_scenario,
            target_app=app_instance,
        )
        scenarios.append(res2)

        # Scenario 3: Prometheus Metrics Scraping
        res3 = await self.run_scenario(
            endpoint="/metrics",
            method="GET",
            concurrency=concurrency,
            total_requests=requests_per_scenario,
            target_app=app_instance,
        )
        scenarios.append(res3)

        total_duration = round(time.perf_counter() - start_time, 3)
        total_reqs = sum(s.total_requests for s in scenarios)
        avg_rps = round(total_reqs / max(total_duration, 0.0001), 2)
        overall_status = "PASS" if all(s.sla_passed for s in scenarios) else "FAIL"

        report = BenchmarkReport(
            timestamp=datetime.now(UTC).isoformat(),
            overall_status=overall_status,
            total_requests=total_reqs,
            total_duration_seconds=total_duration,
            average_rps=avg_rps,
            scenarios=scenarios,
        )

        self._latest_report = report
        return report


_global_profiling_service: ProfilingService | None = None


def get_profiling_service() -> ProfilingService:
    """Dependency provider returning singleton instance of ProfilingService."""
    global _global_profiling_service
    if _global_profiling_service is None:
        _global_profiling_service = ProfilingService()
    return _global_profiling_service
