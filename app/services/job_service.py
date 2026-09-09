"""Job Service orchestrating Priority-Based Background Job Scheduling and Execution."""

import logging
import time
from datetime import UTC, datetime
from typing import Any

from redis.asyncio import Redis

from app.core.dsa.distributed_lock import DistributedLock
from app.core.dsa.priority_queue import (
    JobPriority,
    PriorityJob,
    PriorityJobScheduler,
)
from app.core.exceptions import DistributedLockConflictException

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
        redis_client: Redis | None = None,
    ) -> None:
        self._scheduler: PriorityJobScheduler = scheduler if scheduler is not None else get_priority_job_scheduler()
        self._redis: Redis | None = redis_client
        self._processed_history: list[dict[str, Any]] = []

    async def _get_redis(self) -> Redis:
        """Resolve active Redis client instance."""
        if self._redis is not None:
            return self._redis
        import app.core.redis as r_mod

        if r_mod._redis_client is None:
            await r_mod.init_redis_pool()
        if r_mod._redis_client is None:
            raise RuntimeError("Redis client uninitialized")
        return r_mod._redis_client

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

    async def execute_exclusive_job(
        self,
        job_name: str,
        payload: dict[str, Any],
        ttl_ms: int = 5000,
    ) -> dict[str, Any]:
        """Execute a job exclusively across a distributed cluster using Redis Redlock Mutex.

        Args:
            job_name: Unique logical job name to synchronize across workers.
            payload: Job payload data.
            ttl_ms: Distributed lock TTL in milliseconds (default 5000ms).

        Raises:
            DistributedLockConflictException: If the lock cannot be acquired (resource busy).

        Returns:
            dict containing execution metadata and processed payload.
        """
        redis = await self._get_redis()
        lock = DistributedLock(redis=redis, name=job_name, ttl_ms=ttl_ms)

        acquired = await lock.acquire()
        if not acquired:
            raise DistributedLockConflictException()

        start_time = time.time()
        token = lock.token or ""
        try:
            result = {
                "job_name": job_name,
                "status": "completed",
                "execution_token": token,
                "payload": payload,
                "executed_at": datetime.now(UTC),
                "duration_ms": round((time.time() - start_time) * 1000, 2),
            }
            self._processed_history.append(result)
            return result
        finally:
            await lock.release()


_global_job_service = JobService()


def get_job_service() -> JobService:
    """Singleton provider for global JobService."""
    return _global_job_service


__all__ = [
    "JobService",
    "get_job_service",
    "get_priority_job_scheduler",
]
