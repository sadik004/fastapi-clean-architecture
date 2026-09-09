"""Distributed Rate Limiter services: Sliding Window Log (ZSET) & Token Bucket (Lua Script)."""

from __future__ import annotations

import logging
import time
import uuid
from typing import Any

from redis.asyncio import Redis

logger = logging.getLogger(__name__)


class RateLimiterService:
    """Production-grade Distributed Rate Limiter service.

    Provides two high-performance rate limiting strategies:
    1. Sliding Window Log (Redis ZSET & Atomic Pipelines): Sub-millisecond continuous rolling horizon.
    2. Token Bucket (Redis Lua Scripts with EVALSHA): Strict O(1) memory footprint (< 100 bytes/client)
       and burst-friendly traffic shaping.
    """

    BUCKET_LUA_SCRIPT: str = """
local key = KEYS[1]
local capacity = tonumber(ARGV[1])
local refill_rate = tonumber(ARGV[2])
local requested = tonumber(ARGV[3])
local now = tonumber(ARGV[4])

local data = redis.call('HMGET', key, 'tokens', 'last_updated')
local tokens = tonumber(data[1])
local last_updated = tonumber(data[2])

if not tokens or not last_updated then
    tokens = capacity
    last_updated = now
else
    local delta = math.max(0, now - last_updated)
    tokens = math.min(capacity, tokens + (delta * refill_rate))
    last_updated = now
end

local ttl = 3600
if refill_rate > 0 then
    ttl = math.ceil(capacity / refill_rate) + 2
end

if tokens >= requested then
    local new_tokens = tokens - requested
    redis.call('HMSET', key, 'tokens', tostring(new_tokens), 'last_updated', tostring(last_updated))
    redis.call('EXPIRE', key, ttl)
    return {1, tostring(new_tokens), "0"}
else
    local retry_after = 3600
    if refill_rate > 0 then
        retry_after = (requested - tokens) / refill_rate
    end
    redis.call('HMSET', key, 'tokens', tostring(tokens), 'last_updated', tostring(last_updated))
    redis.call('EXPIRE', key, ttl)
    return {0, tostring(tokens), tostring(retry_after)}
end
"""

    def __init__(self, redis_client: Redis) -> None:
        self._redis = redis_client
        self._token_bucket_sha: str | None = None

    @staticmethod
    def _format_key(key: str) -> str:
        """Ensure standard Redis namespace key format."""
        if not key.startswith("ratelimit:"):
            return f"ratelimit:{key}"
        return key

    # =========================================================================
    # Strategy 1: Distributed Sliding Window Log (Redis ZSET)
    # =========================================================================

    async def check_distributed_rate_limit(
        self,
        key: str,
        limit: int,
        window_seconds: float,
        now: float | None = None,
    ) -> tuple[bool, int, float]:
        """Atomically check and record a request in the distributed sliding window.

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
        """Inspect the real-time sliding window state for a specific client key."""
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

    # =========================================================================
    # Strategy 2: Distributed Token Bucket (Redis Atomic Lua Script & EVALSHA)
    # =========================================================================

    async def _get_or_load_token_bucket_script(self) -> str:
        """Pre-load and cache the Token Bucket Lua script SHA identifier."""
        if self._token_bucket_sha is None:
            self._token_bucket_sha = str(await self._redis.script_load(self.BUCKET_LUA_SCRIPT))
        return self._token_bucket_sha

    async def check_token_bucket(
        self,
        key: str,
        capacity: float,
        refill_rate: float,
        requested: float = 1.0,
        now: float | None = None,
    ) -> tuple[bool, float, float]:
        """Atomically check and consume tokens from client bucket using Redis Lua script.

        Args:
            key: Client or route identifier.
            capacity: Maximum tokens the bucket can hold (burst limit).
            refill_rate: Rate at which tokens are replenished per second.
            requested: Tokens consumed by this request (default: 1.0).
            now: Optional explicit timestamp for deterministic testing.

        Returns:
            tuple[bool, float, float]:
                - is_limited (bool): True if bucket exhausted (HTTP 429), False if allowed.
                - remaining_tokens (float): Available tokens after this operation.
                - retry_after (float): Seconds to wait until sufficient tokens replenish (0.0 if allowed).

        Complexity:
            - Time Complexity: O(1) atomic Lua execution on Redis single thread.
            - Space Complexity: O(1) space (< 100 bytes in Redis Hash storing tokens and last_updated).
        """
        current_time = time.time() if now is None else now
        redis_key = f"ratelimit:tokenbucket:{key}" if not key.startswith("ratelimit:") else key
        sha = await self._get_or_load_token_bucket_script()

        try:
            res: list[Any] = await self._redis.evalsha(
                sha, 1, redis_key, capacity, refill_rate, requested, current_time
            )
        except Exception as exc:
            if "NOSCRIPT" in str(exc):
                self._token_bucket_sha = str(await self._redis.script_load(self.BUCKET_LUA_SCRIPT))
                res = await self._redis.evalsha(
                    self._token_bucket_sha, 1, redis_key, capacity, refill_rate, requested, current_time
                )
            else:
                raise

        allowed = int(res[0]) == 1
        tokens_val = float(res[1])
        retry_val = float(res[2])
        is_limited = not allowed
        return is_limited, tokens_val, retry_val

    async def get_token_bucket_status(
        self,
        key: str,
        capacity: float,
        refill_rate: float,
        now: float | None = None,
    ) -> dict[str, Any]:
        """Inspect the current token bucket state for a client key."""
        current_time = time.time() if now is None else now
        redis_key = f"ratelimit:tokenbucket:{key}" if not key.startswith("ratelimit:") else key

        data = await self._redis.hmget(redis_key, ["tokens", "last_updated"])
        ttl = int(await self._redis.ttl(redis_key))

        raw_tokens = data[0]
        raw_last_updated = data[1]

        if raw_tokens is None or raw_last_updated is None:
            return {
                "key": redis_key,
                "tokens": capacity,
                "capacity": capacity,
                "refill_rate": refill_rate,
                "ttl_seconds": ttl,
                "last_updated": None,
            }

        stored_tokens = float(raw_tokens)
        last_updated = float(raw_last_updated)
        delta = max(0.0, current_time - last_updated)
        effective_tokens = min(capacity, stored_tokens + (delta * refill_rate))

        return {
            "key": redis_key,
            "tokens": effective_tokens,
            "capacity": capacity,
            "refill_rate": refill_rate,
            "ttl_seconds": ttl,
            "last_updated": last_updated,
        }
