"""Analytics Service implementing Write-Behind (Write-Back) asynchronous batch aggregation."""

from __future__ import annotations

import logging
from typing import Any

from redis.asyncio import Redis

from app.repositories.user_repository import UserRepositoryProtocol

logger = logging.getLogger(__name__)

REDIS_PENDING_VIEWS_KEY: str = "user:views:pending"
REDIS_DIRTY_VIEWS_KEY: str = "user:views:dirty"


class AnalyticsService:
    """Service encapsulating high-throughput Write-Behind telemetry and batch aggregation.

    Mechanics:
    1. Write-Behind Ingestion (record_view):
       - Increments in-memory counter in Redis via HINCRBY user:views:pending {user_id} 1
       - Records dirty user ID in Redis Set SADD user:views:dirty {user_id}
       - Resolves in < 1ms without blocking on relational database I/O.
    2. Atomic Batch Flusher (sync_pending_views_to_db):
       - Atomically extracts all pending view counts and clears the dirty set and pending hash
         using a transactional pipeline (MULTI ... EXEC) to prevent lost updates from concurrent writes.
       - Collapses hundreds or thousands of individual view writes into a single bulk database update.
    """

    def __init__(self, redis_client: Redis, repository: UserRepositoryProtocol) -> None:
        self._redis: Redis = redis_client
        self._repo: UserRepositoryProtocol = repository

    async def record_view(self, user_id: int) -> dict[str, Any]:
        """Record profile view using Write-Behind pattern.

        Complexity: O(1) in-memory Redis write. Latency < 1ms.
        """
        user_id_str = str(user_id)
        # Fast in-memory write
        try:
            await self._redis.hincrby(REDIS_PENDING_VIEWS_KEY, user_id_str, 1)
            await self._redis.sadd(REDIS_DIRTY_VIEWS_KEY, user_id_str)
        except Exception as exc:
            logger.warning("Failed to record write-behind view in Redis: %s", exc)
            # Direct database fallback if Redis is unavailable
            await self._repo.increment_views_batch({user_id: 1})
            return {
                "user_id": user_id,
                "status": "recorded",
                "mode": "database-fallback",
            }

        return {
            "user_id": user_id,
            "status": "recorded",
            "mode": "write-behind",
        }

    async def sync_pending_views_to_db(self) -> dict[str, Any]:
        """Extract accumulated view counts atomically and batch-flush to database repository.

        Uses Redis MULTI ... EXEC pipeline to extract and purge keys atomically, guaranteeing
        zero lost updates if concurrent views arrive while flushing.

        Complexity: O(M) where M is the number of dirty users.
        """
        try:
            async with self._redis.pipeline(transaction=True) as pipe:
                pipe.hgetall(REDIS_PENDING_VIEWS_KEY)
                pipe.delete(REDIS_PENDING_VIEWS_KEY)
                pipe.delete(REDIS_DIRTY_VIEWS_KEY)
                results = await pipe.execute()
                raw_views: dict[Any, Any] = results[0] if results else {}
        except Exception as exc:
            logger.error("Failed to extract pending views from Redis: %s", exc)
            return {
                "flushed_records": 0,
                "total_views": 0,
                "status": "redis_error",
            }

        views_map: dict[int, int] = {}
        for uid_raw, count_raw in raw_views.items():
            try:
                uid = int(uid_raw)
                cnt = int(count_raw)
                if cnt > 0:
                    views_map[uid] = cnt
            except (ValueError, TypeError):
                continue

        if not views_map:
            return {
                "flushed_records": 0,
                "total_views": 0,
                "status": "no_pending_data",
            }

        flushed_count = await self._repo.increment_views_batch(views_map)
        total_views_flushed = sum(views_map.values())

        return {
            "flushed_records": flushed_count,
            "total_views": total_views_flushed,
            "status": "success",
        }

    async def get_user_views(self, user_id: int) -> dict[str, int]:
        """Fetch persistent, pending, and total combined views for a user.

        Complexity: O(1) time complexity.
        """
        persistent = await self._repo.get_views(user_id)
        pending = 0
        try:
            raw_pending = await self._redis.hget(REDIS_PENDING_VIEWS_KEY, str(user_id))
            if raw_pending is not None:
                pending = int(raw_pending)
        except Exception as exc:
            logger.debug("Failed to read pending views from Redis: %s", exc)

        return {
            "user_id": user_id,
            "persistent_views": persistent,
            "pending_views": pending,
            "total_views": persistent + pending,
        }
