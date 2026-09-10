"""CLI Script for Running Headless Load Tests & Performance Profiling.

Executes concurrent in-process or network load profiles against high-concurrency endpoints
and outputs a formatted ASCII performance and SLA summary table.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# Ensure repository root is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.main import app
from app.services.profiling_service import ProfilingService


async def main() -> int:
    """Run CLI benchmark suite and report SLA compliance."""
    print("=" * 80)
    print(" FastAPI Enterprise Load Testing & Performance Profiling Harness (Locust / Headless)")
    print("=" * 80)
    print("Configuring concurrent load runner (Concurrency: 25, Volume: 150 req/endpoint)...")

    service = ProfilingService(app=app)
    report = await service.run_full_profile_suite(concurrency=25, requests_per_scenario=150)

    print("\n" + "-" * 80)
    print(f" Benchmark Suite Completed at: {report.timestamp}")
    print(f" Overall Status: {report.overall_status}")
    print(f" Total Requests: {report.total_requests}")
    print(f" Total Duration: {report.total_duration_seconds:.2f}s | Average RPS: {report.average_rps:.1f} req/s")
    print("-" * 80)
    print(f"{'Endpoint':<35} | {'RPS':<8} | {'P50 (ms)':<9} | {'P95 (ms)':<9} | {'P99 (ms)':<9} | {'SLA'}")
    print("-" * 80)

    for sc in report.scenarios:
        sla_str = "PASS" if sc.sla_passed else "FAIL"
        stats = sc.latency_stats
        print(
            f"{sc.endpoint:<35} | {sc.requests_per_second:<8.1f} | "
            f"{stats.p50:<9.2f} | {stats.p95:<9.2f} | {stats.p99:<9.2f} | {sla_str}"
        )
    print("-" * 80)

    if report.overall_status == "PASS":
        print("\nSUCCESS: All endpoints fulfilled the P95 <= 100ms latency and 0.1% error rate SLA.\n")
        return 0
    else:
        print("\nFAILURE: One or more endpoints breached performance SLA thresholds.\n")
        return 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
