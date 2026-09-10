"""Unit and integration tests for Bulkhead Isolation Pattern (Day 62).

Validates:
1. Strict concurrency clamping with O(1) fast rejection (BulkheadFullException)
2. Zero slot leakage on task success and exception failure
3. Cross-compartment non-interference (heavy vs light isolation)
4. Bounded queue waiting semantics when max_queue > 0
5. HTTP 503 response and Retry-After header integration
6. Bulkhead live telemetry and metrics extraction
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx
import pytest

from app.core.exceptions import BulkheadFullException
from app.core.resilience.bulkhead import Bulkhead
from app.main import app
from app.services.resilient_report_service import ResilientReportService


@pytest.mark.asyncio
async def test_bulkhead_concurrency_clamping_strict_fail_fast() -> None:
    """Verify bulkhead clamps concurrency at max_concurrent and rejects excess immediately."""
    bulkhead = Bulkhead(name="test_clamp", max_concurrent=2, max_queue=0)

    started_events = [asyncio.Event(), asyncio.Event()]
    release_event = asyncio.Event()

    async def slow_task(idx: int) -> str:
        async with bulkhead:
            started_events[idx].set()
            await release_event.wait()
            return f"task_{idx}_done"

    # Start 2 tasks that hold the slots
    t0 = asyncio.create_task(slow_task(0))
    t1 = asyncio.create_task(slow_task(1))

    await started_events[0].wait()
    await started_events[1].wait()

    assert bulkhead.active_count == 2
    assert bulkhead.rejections_count == 0

    # 3rd task must be rejected immediately in O(1)
    with pytest.raises(BulkheadFullException) as exc_info:
        async with bulkhead:
            pass

    assert exc_info.value.code == "BULKHEAD_CAPACITY_EXCEEDED"
    assert exc_info.value.retry_after == 5
    assert exc_info.value.compartment == "test_clamp"
    assert bulkhead.rejections_count == 1
    assert bulkhead.active_count == 2

    # Release slots
    release_event.set()
    res0 = await t0
    res1 = await t1

    assert res0 == "task_0_done"
    assert res1 == "task_1_done"
    assert bulkhead.active_count == 0


@pytest.mark.asyncio
async def test_bulkhead_slot_release_on_completion() -> None:
    """Verify slots are cleanly freed when executions complete successfully."""
    bulkhead = Bulkhead(name="test_release", max_concurrent=1, max_queue=0)

    async def quick_task() -> int:
        return 42

    res = await bulkhead.execute(quick_task)
    assert res == 42
    assert bulkhead.active_count == 0

    # Can execute again without error
    res2 = await bulkhead.execute(quick_task)
    assert res2 == 42
    assert bulkhead.active_count == 0


@pytest.mark.asyncio
async def test_bulkhead_slot_release_on_exception() -> None:
    """Verify slots are guaranteed to be released in finally block even when exceptions occur."""
    bulkhead = Bulkhead(name="test_exception", max_concurrent=1, max_queue=0)

    async def exploding_task() -> None:
        raise ValueError("Downstream computation blew up")

    with pytest.raises(ValueError, match="Downstream computation blew up"):
        await bulkhead.execute(exploding_task)

    # Slot must be cleanly released
    assert bulkhead.active_count == 0
    assert bulkhead.rejections_count == 0

    # Next execution succeeds normally
    async def healthy_task() -> str:
        return "recovered"

    result = await bulkhead.execute(healthy_task)
    assert result == "recovered"
    assert bulkhead.active_count == 0


@pytest.mark.asyncio
async def test_bulkhead_cross_compartment_non_interference() -> None:
    """Verify heavy bulkhead saturation does NOT impact or starve the light bulkhead."""
    service = ResilientReportService()
    service.reset()

    # Heavy bulkhead: max_concurrent = 2
    # Light bulkhead: max_concurrent = 20
    release_heavy = asyncio.Event()

    async def mock_heavy_job(report_id: str) -> dict[str, Any]:
        async with service.heavy_bulkhead:
            await release_heavy.wait()
            return {"report_id": report_id, "status": "completed"}

    t_heavy1 = asyncio.create_task(mock_heavy_job("heavy_1"))
    t_heavy2 = asyncio.create_task(mock_heavy_job("heavy_2"))

    # Wait for heavy slots to be claimed
    while service.heavy_bulkhead.active_count < 2:
        await asyncio.sleep(0.005)

    assert service.heavy_bulkhead.active_count == 2

    # 3rd heavy job should fail fast
    with pytest.raises(BulkheadFullException):
        await service.generate_heavy_report("heavy_3", duration_seconds=0.01)

    # Meanwhile, critical light bulkhead tasks MUST succeed immediately without delay
    start_time = time.perf_counter()
    critical_statuses = await asyncio.gather(
        service.get_critical_status(),
        service.get_critical_status(),
        service.get_critical_status(),
    )
    elapsed = time.perf_counter() - start_time

    assert len(critical_statuses) == 3
    for status in critical_statuses:
        assert status["status"] == "completed"
        assert status["compartment"] == "light_critical"

    # Execution should be nearly instant (well under 100ms)
    assert elapsed < 0.1

    # Cleanup heavy tasks
    release_heavy.set()
    await t_heavy1
    await t_heavy2
    assert service.heavy_bulkhead.active_count == 0


@pytest.mark.asyncio
async def test_bulkhead_max_queue_buffering() -> None:
    """Verify max_queue allows bounded waiting before rejecting."""
    bulkhead = Bulkhead(name="test_queue", max_concurrent=1, max_queue=1)

    release_slot = asyncio.Event()

    async def first_job() -> str:
        async with bulkhead:
            await release_slot.wait()
            return "job1"

    async def queued_job() -> str:
        async with bulkhead:
            return "job2"

    t1 = asyncio.create_task(first_job())

    # Wait for t1 to occupy the slot
    while bulkhead.active_count < 1:
        await asyncio.sleep(0.005)

    assert bulkhead.active_count == 1
    assert bulkhead.waiting_count == 0

    # Start t2 - should enter queue
    t2 = asyncio.create_task(queued_job())

    while bulkhead.waiting_count < 1:
        await asyncio.sleep(0.005)

    assert bulkhead.active_count == 1
    assert bulkhead.waiting_count == 1

    # 3rd task exceeds max_queue=1, rejected immediately
    with pytest.raises(BulkheadFullException):
        async with bulkhead:
            pass

    assert bulkhead.rejections_count == 1

    # Release first slot -> queued job acquires and finishes
    release_slot.set()
    res1 = await t1
    res2 = await t2

    assert res1 == "job1"
    assert res2 == "job2"
    assert bulkhead.active_count == 0
    assert bulkhead.waiting_count == 0


@pytest.mark.asyncio
async def test_bulkhead_decorator_interface() -> None:
    """Verify @bulkhead.decorate and @bulkhead.decorate() wrap async functions properly."""
    bulkhead = Bulkhead(name="test_decorator", max_concurrent=1, max_queue=0)

    @bulkhead.decorate()
    async def add_numbers(a: int, b: int) -> int:
        return a + b

    @bulkhead.decorate
    async def multiply_numbers(a: int, b: int) -> int:
        return a * b

    assert await add_numbers(15, 27) == 42
    assert await multiply_numbers(6, 7) == 42
    assert bulkhead.active_count == 0


@pytest.mark.asyncio
async def test_bulkhead_metrics_and_reset() -> None:
    """Verify live metrics telemetry dictionary and reset logic."""
    bulkhead = Bulkhead(name="metrics_test", max_concurrent=1, max_queue=0)

    metrics = bulkhead.get_metrics()
    assert metrics["name"] == "metrics_test"
    assert metrics["max_concurrent"] == 1
    assert metrics["max_queue"] == 0
    assert metrics["active_count"] == 0
    assert metrics["waiting_count"] == 0
    assert metrics["rejections_count"] == 0
    assert metrics["available_slots"] == 1

    # Occupy slot and trigger rejection
    async with bulkhead:
        assert bulkhead.active_count == 1
        assert bulkhead.get_metrics()["available_slots"] == 0
        with pytest.raises(BulkheadFullException):
            async with bulkhead:
                pass

    metrics2 = bulkhead.get_metrics()
    assert metrics2["rejections_count"] == 1
    assert metrics2["active_count"] == 0
    assert metrics2["total_successes"] == 1

    bulkhead.reset()
    assert bulkhead.rejections_count == 0
    assert bulkhead.total_successes == 0


# ==============================================================================
# HTTP Integration Tests
# ==============================================================================


@pytest.mark.asyncio
async def test_http_bulkhead_heavy_job_and_rejection() -> None:
    """Verify HTTP endpoints clamp heavy requests with HTTP 503 and Retry-After header."""
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Reset metrics
        res_metrics = await client.get("/metrics/bulkhead")
        assert res_metrics.status_code == 200
        data = res_metrics.json()
        assert "heavy_reporting" in data["compartments"]
        assert "light_critical" in data["compartments"]

        # 2. Fire 2 long heavy jobs concurrently
        async def launch_heavy_req(report_id: str) -> httpx.Response:
            return await client.post(
                "/resilience/bulkhead/heavy-job",
                json={"report_id": report_id, "duration_seconds": 0.4},
            )

        t1 = asyncio.create_task(launch_heavy_req("rep_alpha"))
        t2 = asyncio.create_task(launch_heavy_req("rep_beta"))

        # Wait briefly for them to occupy the 2 slots
        await asyncio.sleep(0.08)

        # 3. 3rd heavy job must fail fast with HTTP 503
        resp3 = await client.post(
            "/resilience/bulkhead/heavy-job",
            json={"report_id": "rep_gamma", "duration_seconds": 0.1},
        )

        assert resp3.status_code == 503
        err_body = resp3.json()
        assert err_body["error"]["code"] == "BULKHEAD_CAPACITY_EXCEEDED"
        assert "bulkhead capacity" in err_body["detail"].lower()
        assert resp3.headers.get("retry-after") == "5"

        # 4. Critical status MUST still return HTTP 200 OK without delay
        crit_resp = await client.get("/resilience/bulkhead/critical-status")
        assert crit_resp.status_code == 200
        assert crit_resp.json()["status"] == "completed"
        assert crit_resp.json()["compartment"] == "light_critical"

        # 5. Await first 2 tasks
        r1 = await t1
        r2 = await t2
        assert r1.status_code == 200
        assert r2.status_code == 200
        assert r1.json()["status"] == "completed"
        assert r2.json()["status"] == "completed"
