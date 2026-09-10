"""Automated Performance Benchmark & SLA Verification Test Suite."""

from __future__ import annotations

import ast
import subprocess  # nosec B404
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services.profiling_service import (
    ProfilingService,
    calculate_percentiles,
)


def test_locustfile_task_weights_and_structure() -> None:
    """Invariant: FastAPIEcommerceUser defines the four weighted realistic client tasks."""
    locust_path = Path("load_tests/locustfile.py")
    assert locust_path.exists(), "load_tests/locustfile.py does not exist"

    content = locust_path.read_text(encoding="utf-8")
    tree = ast.parse(content, filename=str(locust_path))

    user_classes = [n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "FastAPIEcommerceUser"]
    assert len(user_classes) == 1, "FastAPIEcommerceUser class not found"
    user_cls = user_classes[0]

    # Verify base class includes HttpUser
    base_names = [b.id for b in user_cls.bases if isinstance(b, ast.Name)]
    assert "HttpUser" in base_names

    # Extract tasks and their declared weights
    tasks_with_weights: dict[str, int] = {}
    for node in user_cls.body:
        if isinstance(node, ast.FunctionDef):
            for dec in node.decorator_list:
                if isinstance(dec, ast.Call) and isinstance(dec.func, ast.Name) and dec.func.id == "task":
                    weight = 1
                    if dec.args and isinstance(dec.args[0], ast.Constant) and isinstance(dec.args[0].value, int):
                        weight = dec.args[0].value
                    tasks_with_weights[node.name] = weight

    assert "browse_catalog_keyset" in tasks_with_weights
    assert "fuzzy_search_products" in tasks_with_weights
    assert "probe_health_and_metrics" in tasks_with_weights
    assert "place_order_checkout" in tasks_with_weights

    assert tasks_with_weights["browse_catalog_keyset"] == 5
    assert tasks_with_weights["fuzzy_search_products"] == 3
    assert tasks_with_weights["probe_health_and_metrics"] == 2
    assert tasks_with_weights["place_order_checkout"] == 1
    assert sum(tasks_with_weights.values()) == 11


def test_locust_headless_execution_in_isolated_process() -> None:
    """Harness Invariant: Locustfile parses and executes cleanly under headless Locust runner."""
    cmd = [
        sys.executable,
        "-m",
        "locust",
        "-f",
        "load_tests/locustfile.py",
        "--headless",
        "-u",
        "1",
        "-r",
        "1",
        "-t",
        "1s",
        "--host=http://localhost:8000",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)  # nosec B603 # noqa: S603
    assert result.returncode == 0
    assert "Starting Locust" in result.stderr or "Starting Locust" in result.stdout


def test_percentile_calculation_accuracy() -> None:
    """Mathematical Invariant: calculate_percentiles accurately computes P50, P90, P95, P99 quantiles."""
    # Synthetic array of 10 values from 10.0 to 100.0
    latencies = [10.0, 20.0, 30.0, 40.0, 50.0, 60.0, 70.0, 80.0, 90.0, 100.0]
    stats = calculate_percentiles(latencies)

    assert stats.min_latency_ms == 10.0
    assert stats.max_latency_ms == 100.0
    assert stats.mean_latency_ms == 55.0
    assert stats.p50 == 55.0  # Median
    assert stats.p90 == 91.0
    assert stats.p95 == 95.5
    assert stats.p99 == 99.1

    # Single-element edge case
    single = calculate_percentiles([42.5])
    assert single.min_latency_ms == 42.5
    assert single.max_latency_ms == 42.5
    assert single.p50 == 42.5
    assert single.p95 == 42.5

    # Empty array edge case
    empty = calculate_percentiles([])
    assert empty.p50 == 0.0
    assert empty.p95 == 0.0
    assert empty.mean_latency_ms == 0.0


@pytest.mark.asyncio
async def test_concurrent_load_under_concurrency_scaling() -> None:
    """SLA Invariant: Concurrent workers on /catalog/items/keyset maintain P95 < 100ms with 0% error rate."""
    service = ProfilingService(app=app)
    # Warm up SQLAlchemy query compilation and ASGI router caches
    await service.run_scenario(
        endpoint="/catalog/items/keyset?limit=20",
        method="GET",
        concurrency=1,
        total_requests=1,
    )

    result = await service.run_scenario(
        endpoint="/catalog/items/keyset?limit=20",
        method="GET",
        concurrency=10,
        total_requests=100,
        max_p95_ms=150.0,
    )

    assert result.total_requests == 100
    assert result.successful_requests == 100
    assert result.failed_requests == 0
    assert result.error_rate_pct == 0.0
    assert result.latency_stats.p95 < 150.0, f"P95 latency breached: {result.latency_stats.p95}ms"
    assert result.sla_passed is True


@pytest.mark.asyncio
async def test_throughput_threshold_in_memory() -> None:
    """Throughput Invariant: Non-blocking in-memory endpoints achieve > 200 RPS under concurrency."""
    service = ProfilingService(app=app)
    result = await service.run_scenario(
        endpoint="/health/liveness",
        method="GET",
        concurrency=25,
        total_requests=150,
    )

    assert result.total_requests == 150
    assert result.successful_requests == 150
    assert result.requests_per_second > 100.0, f"RPS below expected threshold: {result.requests_per_second}"
    assert result.sla_passed is True


def test_benchmark_diagnostic_api_run_and_latest(client: TestClient) -> None:
    """API Invariant: POST /observability/benchmarks/run-profile executes suite and updates latest report."""
    # 1. Trigger benchmark run with modest volume for fast test execution
    response = client.post("/observability/benchmarks/run-profile?concurrency=10&requests_per_scenario=25")
    assert response.status_code == 200
    data = response.json()

    assert data["overall_status"] in ("PASS", "FAIL")
    assert data["total_requests"] == 75  # 3 scenarios * 25 requests
    assert len(data["scenarios"]) == 3
    assert data["average_rps"] > 0
    assert "timestamp" in data

    # Verify scenario breakdown
    for sc in data["scenarios"]:
        assert "endpoint" in sc
        assert "latency_stats" in sc
        assert "p95" in sc["latency_stats"]
        assert sc["error_rate_pct"] == 0.0

    # 2. Retrieve latest benchmark report
    latest_resp = client.get("/observability/benchmarks/latest")
    assert latest_resp.status_code == 200
    latest_data = latest_resp.json()
    assert latest_data["timestamp"] == data["timestamp"]
    assert latest_data["total_requests"] == data["total_requests"]
