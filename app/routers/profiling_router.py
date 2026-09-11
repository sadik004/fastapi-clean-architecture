"""API Router for Performance Profiling & Headless Load Testing Telemetry."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request, status

from app.schemas.profiling import BenchmarkReportResponse
from app.services.profiling_service import (
    BenchmarkReport,
    ProfilingService,
    get_profiling_service,
)

router = APIRouter(prefix="/observability/benchmarks", tags=["Observability - Performance Benchmarking"])


@router.post(
    "/run-profile",
    response_model=BenchmarkReportResponse,
    status_code=status.HTTP_200_OK,
    summary="Execute Controlled In-Process Load Benchmark",
    description="Dispatches a concurrent benchmark suite against high-throughput endpoints and returns SLA percentiles.",
)
async def trigger_run_profile(
    request: Request,
    concurrency: Annotated[int, Query(ge=1, le=100, description="Concurrent workers (1-100)")] = 25,
    requests_per_scenario: Annotated[int, Query(ge=10, le=500, description="Requests per endpoint")] = 100,
    service: ProfilingService = Depends(get_profiling_service),
) -> dict[str, Any]:
    """Execute concurrent load profile and return percentile performance telemetry."""
    report: BenchmarkReport = await service.run_full_profile_suite(
        target_app=request.app,
        concurrency=concurrency,
        requests_per_scenario=requests_per_scenario,
    )
    return report.to_dict()


@router.get(
    "/latest",
    response_model=BenchmarkReportResponse | dict[str, Any],
    status_code=status.HTTP_200_OK,
    summary="Retrieve Latest Benchmark Report",
    description="Returns the most recently executed performance benchmark and SLA metrics.",
)
async def get_latest_benchmark_report(
    service: ProfilingService = Depends(get_profiling_service),
) -> dict[str, Any]:
    """Retrieve the latest benchmark execution report."""
    report = service.get_latest_report()
    if report is None:
        return {
            "status": "NO_BENCHMARKS_RECORDED",
            "message": "No load test has been executed yet. Trigger POST /observability/benchmarks/run-profile to generate baseline.",
        }
    return report.to_dict()
