"""Priority Queue and Job Scheduling Data Structure utilizing Python's binary min-heap (heapq).

Provides:
- JobPriority: Integer enumeration for task urgency levels (CRITICAL=1, HIGH=2, NORMAL=3, LOW=4).
- PriorityJob: Memory-optimized slotted dataclass with stable FIFO tie-breaking and payload compare isolation.
- PriorityJobScheduler: Asynchronously synchronized binary min-heap job scheduler with O(log N) operations.
"""

import asyncio
from dataclasses import dataclass, field
from enum import IntEnum
import heapq
import time
from typing import Any, Optional


class JobPriority(IntEnum):
    """Priority levels for scheduled background jobs.

    Lower numeric values yield higher precedence in binary min-heap ordering.
    """

    CRITICAL = 1
    HIGH = 2
    NORMAL = 3
    LOW = 4


@dataclass(slots=True, order=True)
class PriorityJob:
    """Slotted job entity designed for binary min-heap prioritization.

    Comparison Invariants:
    1. Primary key: `priority: int` (1=CRITICAL takes precedence over 4=LOW).
    2. Secondary key: `scheduled_at: float` (earlier timestamp takes precedence).
    3. Tertiary tie-breaker: `sequence: int` (guarantees strictly stable FIFO ordering).
    4. Non-comparable fields: `job_id`, `task_type`, `payload`, `retries` are marked with
       `compare=False` to eliminate runtime `TypeError` when evaluating unorderable dictionaries.
    """

    priority: int
    scheduled_at: float
    sequence: int
    job_id: str = field(compare=False)
    task_type: str = field(compare=False)
    payload: dict[str, Any] = field(compare=False)
    retries: int = field(default=0, compare=False)


class PriorityJobScheduler:
    """High-performance in-memory Priority Queue backed by a binary min-heap (heapq).

    Time Complexity:
    - Schedule (Push): Strictly O(log N)
    - Peek Root: Strictly O(1)
    - Pop Due Job: Strictly O(log N) when due, O(1) when delayed
    - Size / Empty Check: Strictly O(1)
    """

    __slots__ = ("_heap", "_lock", "_sequence_counter")

    def __init__(self) -> None:
        self._heap: list[PriorityJob] = []
        self._sequence_counter: int = 0
        self._lock: asyncio.Lock = asyncio.Lock()

    def size(self) -> int:
        """Return the current number of scheduled jobs in the queue."""
        return len(self._heap)

    def is_empty(self) -> bool:
        """Check if the queue contains zero pending jobs."""
        return len(self._heap) == 0

    def peek_sync(self) -> Optional[PriorityJob]:
        """Inspect the highest-priority root job in O(1) time without removing it (sync)."""
        if not self._heap:
            return None
        return self._heap[0]

    async def peek(self) -> Optional[PriorityJob]:
        """Inspect the highest-priority root job in O(1) time under async lock."""
        async with self._lock:
            return self.peek_sync()

    def schedule_sync(
        self,
        task_type: str,
        payload: dict[str, Any],
        priority: JobPriority = JobPriority.NORMAL,
        delay_seconds: float = 0.0,
        job_id: Optional[str] = None,
    ) -> str:
        """Synchronously enqueue a job into the binary min-heap in O(log N) time."""
        now = time.time()
        scheduled_at = now + max(delay_seconds, 0.0)
        self._sequence_counter += 1
        jid = job_id or f"job_{self._sequence_counter:08d}"

        job = PriorityJob(
            priority=int(priority),
            scheduled_at=scheduled_at,
            sequence=self._sequence_counter,
            job_id=jid,
            task_type=task_type,
            payload=payload,
        )
        heapq.heappush(self._heap, job)
        return jid

    async def schedule(
        self,
        task_type: str,
        payload: dict[str, Any],
        priority: JobPriority = JobPriority.NORMAL,
        delay_seconds: float = 0.0,
        job_id: Optional[str] = None,
    ) -> str:
        """Asynchronously enqueue a job with thread/coroutine coordination."""
        async with self._lock:
            return self.schedule_sync(
                task_type=task_type,
                payload=payload,
                priority=priority,
                delay_seconds=delay_seconds,
                job_id=job_id,
            )

    def pop_due_job_sync(self, now: Optional[float] = None) -> Optional[PriorityJob]:
        """Synchronously pop the highest priority job if its execution timestamp has arrived.

        Returns:
            PriorityJob if top job scheduled_at <= current_time, otherwise None.
        """
        if not self._heap:
            return None

        current_time = now if now is not None else time.time()
        if self._heap[0].scheduled_at <= current_time:
            return heapq.heappop(self._heap)
        return None

    async def pop_due_job(self, now: Optional[float] = None) -> Optional[PriorityJob]:
        """Asynchronously pop the highest priority due job under lock in O(log N) time."""
        async with self._lock:
            return self.pop_due_job_sync(now=now)

    def clear(self) -> None:
        """Clear all pending jobs and reset sequence counter."""
        self._heap.clear()
        self._sequence_counter = 0


__all__ = [
    "JobPriority",
    "PriorityJob",
    "PriorityJobScheduler",
]
