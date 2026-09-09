"""Distributed Sliding Window Log Rate Limiter service powered by Redis ZSET."""

from __future__ import annotations

import logging
import time
import uuid
from typing import Any

from redis.asyncio import Redis

logger = logging.getLogger(__name__)


class RateLimiterService:
    """Production-grade Distributed Sliding Window Rate Limiter powered by Redis ZSET.

    Eliminates multi-node synchronization blindness and boundary bursts by maintaining a
    continuous rolling timestamp window in a single Redis Sorted Set with atomic pipelines.
    """

    def __init__(self, redis_client: Redis) -> None:
        self._redis = redis_client

    @staticmethod
    def _format_key(key: str) -> str:
        """Ensure standard Redis namespace key format."""
        if not key.startswith("ratelimit:"):
            return f"ratelimit:{key}"
        return key

    async def check_distributed_rate_limit(
        self,
        key: str,
        limit: int,
        window_seconds: float,
        now: float | None = None,
    ) -> tuple[bool, int, float]:
        """Atomically check and record a request in the distributed sliding window.

        Args:
            key: Client or route identifier.
            limit: Maximum allowed requests in the rolling window.
            window_seconds: Duration of the rolling window in seconds.
            now: Optional explicit timestamp for deterministic testing.

        Returns:
            tuple[bool, int, float]:
                - is_limited (bool): True if request exceeds quota (rejected), False if allowed.
                - current_count (int): Active requests in the window.
                - retry_after (float): Seconds remaining until the oldest request expires (0.0 if allowed).

        Complexity:
            - Prune expired entries: O(log N + M) where M is expired items.
            - Insert request: O(log N).
            - Count cardinality: O(1).
            - Total execution: O(log N + M) executed atomically in a single Redis roundtrip.
        """
        current_time = time.time() if now is None else now
        redis_key = self._format_key(key)
        member = f"{current_time}:{uuid.uuid4().hex[:8]}"

        # Atomic Pipeline: prune, tentatively add, count, inspect oldest, and refresh TTL
        async with self._redis.pipeline(transaction=True) as pipe:
            pipe.zremrangebyscore(redis_key, 0, f"({current_time - window_seconds}")
            pipe.zadd(redis_key, {member: current_time})
            pipe.zcard(redis_key)
            pipe.zrange(redis_key, 0, 0, withscores=True)
            pipe.expire(redis_key, int(window_seconds) + 2)
            results = await pipe.execute()

        current_count = int(results[2])
        oldest_entries = results[3]

        if current_count <= limit:
            return False, current_count, 0.0

        # Request exceeds quota: roll back tentative insertion so failed requests don't consume quota
        await self._redis.zrem(redis_key, member)

        oldest_ts = current_time
        if oldest_entries and len(oldest_entries) > 0:
            oldest_ts = float(oldest_entries[0][1])

        retry_after = max(0.0, (oldest_ts + window_seconds) - current_time)
        return True, limit, retry_after

    async def get_client_status(
        self,
        key: str,
        window_seconds: float = 60.0,
        now: float | None = None,
    ) -> dict[str, Any]:
        """Inspect the real-time sliding window state for a specific client key.

        Returns:
            dict containing active_requests, ttl, window_seconds, and timestamps.
        """
        current_time = time.time() if now is None else now
        redis_key = self._format_key(key)

        await self._redis.zremrangebyscore(redis_key, 0, f"({current_time - window_seconds}")
        count = int(await self._redis.zcard(redis_key))
        ttl = int(await self._redis.ttl(redis_key))
        oldest = await self._redis.zrange(redis_key, 0, 0, withscores=True)
        newest = await self._redis.zrevrange(redis_key, 0, 0, withscores=True)

        oldest_ts = float(oldest[0][1]) if oldest else None
        newest_ts = float(newest[0][1]) if newest else None

        return {
            "key": redis_key,
            "active_requests": count,
            "ttl_seconds": ttl,
            "window_seconds": window_seconds,
            "oldest_timestamp": oldest_ts,
            "newest_timestamp": newest_ts,
        }
