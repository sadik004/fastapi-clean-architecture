"""Service layer coordinating ARQ job enqueuing, pool management, and status polling."""

from __future__ import annotations

import logging
import uuid
from typing import Any

from arq import create_pool
from arq.connections import ArqRedis
from arq.jobs import Job, JobStatus

from app.core.arq_app import get_arq_redis_settings
from app.schemas.arq import ArqJobStatusResponse

logger = logging.getLogger(__name__)


class ArqService:
    """Manages the ARQ Redis connection pool and provides job dispatching operations."""

    _pool: ArqRedis | None = None
    _pool_override: Any | None = None

    @classmethod
    async def get_pool(cls) -> Any:
        """Retrieve the active ARQ connection pool, lazily initializing if necessary."""
        if cls._pool_override is not None:
            return cls._pool_override

        if cls._pool is None:
            cls._pool = await create_pool(get_arq_redis_settings())
        return cls._pool

    @classmethod
    def set_pool_override(cls, pool: Any | None) -> None:
        """Override the ARQ pool instance (used in test fixtures for dependency injection)."""
        cls._pool_override = pool

    @classmethod
    async def close_pool(cls) -> None:
        """Gracefully close the cached ARQ connection pool."""
        if cls._pool is not None:
            await cls._pool.aclose()
            cls._pool = None

    @classmethod
    async def enqueue_webhook_task(cls, webhook_url: str, payload: dict[str, Any]) -> str:
        """Enqueue an asynchronous outbound webhook dispatch job.

        Returns the unique job_id string.
        """
        pool = await cls.get_pool()
        job = await pool.enqueue_job("sync_webhook_notification", webhook_url, payload)
        if job is None:
            # Fallback if custom job_id collision occurred
            return uuid.uuid4().hex
        return str(job.job_id)

    @classmethod
    async def enqueue_broadcast_task(cls, user_ids: list[int], message: str) -> str:
        """Enqueue a high-volume asynchronous push notification broadcast job.

        Returns the unique job_id string.
        """
        pool = await cls.get_pool()
        job = await pool.enqueue_job("broadcast_push_notification", user_ids, message)
        if job is None:
            return uuid.uuid4().hex
        return str(job.job_id)

    @classmethod
    async def get_job_status(cls, job_id: str) -> ArqJobStatusResponse:
        """Poll the current execution state and result of an ARQ job."""
        pool = await cls.get_pool()
        job = Job(job_id=job_id, redis=pool)
        status_enum = await job.status()
        status_str = status_enum.value if isinstance(status_enum, JobStatus) else str(status_enum)

        enqueue_time_str: str | None = None
        result_payload: Any | None = None
        success: bool | None = None

        if status_str != "not_found":
            try:
                info = await job.info()
                if info and getattr(info, "enqueue_time", None) is not None:
                    enqueue_time_str = info.enqueue_time.isoformat()
            except Exception:
                enqueue_time_str = None

            if status_str == "complete":
                try:
                    result_info = await job.result_info()
                    if result_info is not None:
                        result_payload = result_info.result
                        success = result_info.success
                except Exception as exc:
                    logger.warning("Failed to retrieve ARQ job result_info for job %s: %s", job_id, exc)

        return ArqJobStatusResponse(
            job_id=job_id,
            status=status_str,
            result=result_payload,
            enqueue_time=enqueue_time_str,
            success=success,
        )
