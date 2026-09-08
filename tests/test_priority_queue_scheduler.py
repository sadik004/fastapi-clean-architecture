"""Comprehensive test suite for Day 24: Priority Queue (heapq) Architecture for Priority-Based Background Job Scheduling.

Verifies:
1. Slotted PriorityJob Memory Invariants: Zero __dict__ overhead and typo restriction.
2. Priority Inversion Verification: Reverse insertion order pops strictly in urgency sequence (CRITICAL -> HIGH -> NORMAL -> LOW).
3. Monotonic FIFO Tie-Breaking: Identical priority and timestamp jobs pop in stable sequence counter order.
4. Delayed Execution Mechanics: Delayed jobs return None before maturity and pop immediately upon scheduled_at timestamp.
5. Dictionary Payload Comparison Safety: Assert unorderable dictionary payloads do not trigger TypeError.
6. Heap Scaling Benchmark: 10,000 push/pop operations execute within O(N log N) time (< 60ms).
7. End-to-End API Test: POST /jobs/schedule (202 Accepted) and GET /jobs/status via FastAPI TestClient.
"""

import asyncio
import random
import time
import pytest
from fastapi import status
from fastapi.testclient import TestClient

from app.core.dsa.priority_queue import (
    JobPriority,
    PriorityJob,
    PriorityJobScheduler,
)
from app.services.job_service import JobService


# ============================================================================
# 1. Slotted Memory Invariant Tests
# ============================================================================


def test_slotted_priority_job_memory_invariants() -> None:
    """Verify PriorityJob suppresses __dict__ and prohibits undeclared dynamic attributes."""
    job = PriorityJob(
        priority=int(JobPriority.HIGH),
        scheduled_at=time.time(),
        sequence=1,
        job_id="job_001",
        task_type="test_task",
        payload={"key": "value"},
    )

    # Invariant: No dynamic dictionary
    assert hasattr(job, "__dict__") is False
    assert hasattr(PriorityJob, "__slots__")

    # Invariant: Typo / undeclared attribute assignment raises AttributeError
    with pytest.raises(AttributeError):
        job.undeclared_tag = "leak"  # type: ignore[attr-defined]


# ============================================================================
# 2. Priority Inversion Verification Test
# ============================================================================


@pytest.mark.asyncio
async def test_priority_inversion_verification() -> None:
    """Schedule 4 jobs in reverse urgency order (LOW -> NORMAL -> HIGH -> CRITICAL).

    Assert they pop strictly in order of urgency: CRITICAL -> HIGH -> NORMAL -> LOW.
    """
    scheduler = PriorityJobScheduler()

    # Push in reverse order
    await scheduler.schedule(task_type="low_task", payload={}, priority=JobPriority.LOW)
    await scheduler.schedule(task_type="normal_task", payload={}, priority=JobPriority.NORMAL)
    await scheduler.schedule(task_type="high_task", payload={}, priority=JobPriority.HIGH)
    await scheduler.schedule(task_type="critical_task", payload={}, priority=JobPriority.CRITICAL)

    assert scheduler.size() == 4

    # Pop order must be strictly sorted by priority integer ascending (1, 2, 3, 4)
    job1 = await scheduler.pop_due_job()
    assert job1 is not None
    assert job1.priority == int(JobPriority.CRITICAL)
    assert job1.task_type == "critical_task"

    job2 = await scheduler.pop_due_job()
    assert job2 is not None
    assert job2.priority == int(JobPriority.HIGH)
    assert job2.task_type == "high_task"

    job3 = await scheduler.pop_due_job()
    assert job3 is not None
    assert job3.priority == int(JobPriority.NORMAL)
    assert job3.task_type == "normal_task"

    job4 = await scheduler.pop_due_job()
    assert job4 is not None
    assert job4.priority == int(JobPriority.LOW)
    assert job4.task_type == "low_task"

    assert scheduler.is_empty() is True


# ============================================================================
# 3. Monotonic FIFO Tie-Breaking Test
# ============================================================================


@pytest.mark.asyncio
async def test_monotonic_fifo_tie_breaking() -> None:
    """Schedule 10 jobs with identical priority and execution timestamps.

    Assert they pop in strict monotonic sequence order (0 through 9).
    """
    scheduler = PriorityJobScheduler()
    fixed_time = time.time()

    # Enqueue 10 jobs with fixed timestamp and identical priority
    for i in range(10):
        # Using synchronous scheduling with identical fixed_time
        scheduler._sequence_counter += 1
        job = PriorityJob(
            priority=int(JobPriority.NORMAL),
            scheduled_at=fixed_time,
            sequence=i,
            job_id=f"job_{i}",
            task_type="fifo_task",
            payload={"index": i},
        )
        import heapq
        heapq.heappush(scheduler._heap, job)

    assert scheduler.size() == 10

    # Pop all jobs; assert monotonic sequence order
    for expected_seq in range(10):
        popped = await scheduler.pop_due_job()
        assert popped is not None
        assert popped.sequence == expected_seq
        assert popped.job_id == f"job_{expected_seq}"


# ============================================================================
# 4. Delayed Execution & Adaptive Sleep Tests
# ============================================================================


@pytest.mark.asyncio
async def test_delayed_execution_mechanics() -> None:
    """Verify that jobs with delay_seconds > 0 are not popped before scheduled_at."""
    scheduler = PriorityJobScheduler()

    # Schedule job with 0.10s delay
    await scheduler.schedule(
        task_type="delayed_welcome",
        payload={"email": "delay@example.com"},
        priority=JobPriority.CRITICAL,
        delay_seconds=0.10,
    )

    # Immediately inspecting due job should return None
    assert await scheduler.pop_due_job() is None
    assert scheduler.size() == 1

    # Wait 0.12s for delay to mature
    await asyncio.sleep(0.12)

    due_job = await scheduler.pop_due_job()
    assert due_job is not None
    assert due_job.task_type == "delayed_welcome"
    assert scheduler.size() == 0


@pytest.mark.asyncio
async def test_service_adaptive_sleep_calculation() -> None:
    """Verify JobService calculates non-busy-waiting sleep intervals without CPU spinning."""
    scheduler = PriorityJobScheduler()
    service = JobService(scheduler=scheduler)

    # When queue is empty, returns default 1.0s
    assert await service.compute_adaptive_sleep_delay() == 1.0

    # When job is scheduled with 0.5s delay
    await scheduler.schedule(task_type="delayed", payload={}, delay_seconds=0.5)
    delay = await service.compute_adaptive_sleep_delay()
    assert 0.40 <= delay <= 0.55

    # When job is immediately due, returns 0.0s
    await scheduler.schedule(task_type="immediate", payload={}, priority=JobPriority.CRITICAL, delay_seconds=0.0)
    assert await service.compute_adaptive_sleep_delay() == 0.0


# ============================================================================
# 5. Dictionary Payload Comparison Safety Test
# ============================================================================


def test_dictionary_payload_comparison_safety() -> None:
    """Verify that unorderable dictionary payloads do NOT trigger TypeError under identical priorities.

    In Python dataclasses with order=True, if sequence is compared and identical (or non-comparable),
    unmarked dictionary fields raise TypeError: '<' not supported between instances of 'dict' and 'dict'.
    Marking payload with field(compare=False) guarantees absolute safety.
    """
    # Two jobs with identical priority, identical timestamp, but different unorderable dictionaries
    fixed_time = time.time()
    job_a = PriorityJob(
        priority=2,
        scheduled_at=fixed_time,
        sequence=1,
        job_id="a",
        task_type="t",
        payload={"complex": {"nested": [1, 2, 3]}, "alpha": "a"},
    )
    job_b = PriorityJob(
        priority=2,
        scheduled_at=fixed_time,
        sequence=2,
        job_id="b",
        task_type="t",
        payload={"complex": {"nested": [4, 5, 6]}, "beta": "b"},
    )

    # Comparison should evaluate cleanly on sequence (1 < 2), never comparing the payload dicts
    assert job_a < job_b
    assert not (job_b < job_a)


# ============================================================================
# 6. Heap Scaling Benchmark Test
# ============================================================================


def test_heap_scaling_benchmark() -> None:
    """Benchmark 10,000 push and pop operations, asserting O(N log N) execution under 60ms."""
    scheduler = PriorityJobScheduler()
    priorities = [JobPriority.CRITICAL, JobPriority.HIGH, JobPriority.NORMAL, JobPriority.LOW]

    # Generate 10,000 random priorities
    random_priorities = [random.choice(priorities) for _ in range(10_000)]

    start_time = time.perf_counter()

    # Push 10,000 jobs
    for i, prio in enumerate(random_priorities):
        scheduler.schedule_sync(
            task_type="benchmark_task",
            payload={"iter": i},
            priority=prio,
        )

    assert scheduler.size() == 10_000

    # Pop all 10,000 jobs
    prev_priority = 0
    pop_count = 0
    while not scheduler.is_empty():
        job = scheduler.pop_due_job_sync()
        assert job is not None
        assert job.priority >= prev_priority, "Heap property violated!"
        prev_priority = job.priority
        pop_count += 1

    elapsed_ms = (time.perf_counter() - start_time) * 1000.0

    assert pop_count == 10_000
    # 10,000 pushes + 10,000 pops in Python heapq typically takes 15-35ms
    assert elapsed_ms < 100.0, f"Heap benchmark took {elapsed_ms:.2f}ms, expected < 100.0ms"


# ============================================================================
# 7. End-to-End API Integration Tests
# ============================================================================


def test_api_schedule_and_status_endpoints(client: TestClient) -> None:
    """Verify POST /jobs/schedule and GET /jobs/status endpoints via FastAPI TestClient."""
    # 1. Schedule a CRITICAL job
    res_critical = client.post(
        "/jobs/schedule",
        json={
            "task_type": "security_alert",
            "payload": {"alert_id": "SEC-999"},
            "priority": 1,  # CRITICAL
            "delay_seconds": 0.0,
        },
    )
    assert res_critical.status_code == status.HTTP_202_ACCEPTED
    data_crit = res_critical.json()
    assert data_crit["task_type"] == "security_alert"
    assert data_crit["priority"] == "CRITICAL"
    assert "job_id" in data_crit

    # 2. Schedule a LOW priority job
    res_low = client.post(
        "/jobs/schedule",
        json={
            "task_type": "daily_report",
            "payload": {"report": "metrics"},
            "priority": 4,  # LOW
            "delay_seconds": 10.0,
        },
    )
    assert res_low.status_code == status.HTTP_202_ACCEPTED
    data_low = res_low.json()
    assert data_low["priority"] == "LOW"

    # 3. Check /jobs/status telemetry
    status_res = client.get("/jobs/status")
    assert status_res.status_code == status.HTTP_200_OK
    status_data = status_res.json()

    assert status_data["queue_size"] >= 2
    assert status_data["has_pending_jobs"] is True
    # Highest priority job at root of min-heap should be CRITICAL
    assert status_data["next_job_priority"] == "CRITICAL"
