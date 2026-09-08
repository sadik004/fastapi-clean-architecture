"""Job Service orchestrating Priority-Based Background Job Scheduling and Execution."""

import logging
import time
from datetime import UTC, datetime
from typing import Any

from app.core.dsa.priority_queue import (
    JobPriority,
    PriorityJob,
    PriorityJobScheduler,
)

logger = logging.getLogger(__name__)

_global_job_scheduler = PriorityJobScheduler()


def get_priority_job_scheduler() -> PriorityJobScheduler:
    """Singleton provider for global PriorityJobScheduler min-heap."""
    return _global_job_scheduler


class JobService:
    """Service handling job scheduling, worker execution, and telemetry reporting."""

    def __init__(
        self,
        scheduler: PriorityJobScheduler | None = None,
    ) -> None:
        self._scheduler: PriorityJobScheduler = scheduler if scheduler is not None else get_priority_job_scheduler()
        self._processed_history: list[dict[str, Any]] = []

    @property
    def scheduler(self) -> PriorityJobScheduler:
        """Access underlying priority scheduler."""
        return self._scheduler

    async def schedule_job(
        self,
        task_type: str,
        payload: dict[str, Any],
        priority: JobPriority = JobPriority.NORMAL,
        delay_seconds: float = 0.0,
        job_id: str | None = None,
    ) -> dict[str, Any]:
        """Schedule a job and return structured response metadata."""
        now = time.time()
        scheduled_at = now + max(delay_seconds, 0.0)
        jid = await self._scheduler.schedule(
            task_type=task_type,
            payload=payload,
            priority=priority,
            delay_seconds=delay_seconds,
            job_id=job_id,
        )

        priority_name = JobPriority(priority).name

        return {
            "job_id": jid,
            "task_type": task_type,
            "priority": priority_name,
            "scheduled_at": datetime.fromtimestamp(scheduled_at, tz=UTC),
            "status": "scheduled",
        }

    async def get_telemetry(self) -> dict[str, Any]:
        """Return O(1) queue status and inspection of top root job."""
        size = self._scheduler.size()
        next_job: PriorityJob | None = await self._scheduler.peek()

        next_priority: str | None = None
        next_scheduled: datetime | None = None

        if next_job is not None:
            next_priority = JobPriority(next_job.priority).name
            next_scheduled = datetime.fromtimestamp(next_job.scheduled_at, tz=UTC)

        return {
            "queue_size": size,
            "has_pending_jobs": size > 0,
            "next_job_priority": next_priority,
            "next_job_scheduled_at": next_scheduled,
        }

    async def process_due_jobs(self, max_jobs: int = 10) -> list[dict[str, Any]]:
        """Pop and execute due jobs in strict priority and FIFO tie-break order.

        Args:
            max_jobs: Maximum batch of due jobs to process in one step.

        Returns:
            List of executed job result dictionaries.
        """
        executed: list[dict[str, Any]] = []

        for _ in range(max_jobs):
            job = await self._scheduler.pop_due_job()
            if job is None:
                break

            result = {
                "job_id": job.job_id,
                "task_type": job.task_type,
                "priority": JobPriority(job.priority).name,
                "executed_at": datetime.now(UTC),
                "payload": job.payload,
            }
            executed.append(result)
            self._processed_history.append(result)
            logger.info("Executed job %s (%s)", job.job_id, job.task_type)

        return executed

    async def compute_adaptive_sleep_delay(self) -> float:
        """Calculate non-busy-waiting sleep delay to avoid burning CPU cycles.

        Returns:
            Sleep duration in seconds:
            - 0.0: If highest priority job is already due.
            - min(max(time_until_due, 0.01), 1.0): If future job is waiting.
            - 1.0: If queue is currently empty.
        """
        next_job = await self._scheduler.peek()
        if next_job is None:
            return 1.0

        now = time.time()
        time_until_due = next_job.scheduled_at - now
        if time_until_due <= 0.0:
            return 0.0

        return min(max(time_until_due, 0.01), 1.0)


_global_job_service = JobService()


def get_job_service() -> JobService:
    """Singleton provider for global JobService."""
    return _global_job_service


__all__ = [
    "JobService",
    "get_job_service",
    "get_priority_job_scheduler",
]
