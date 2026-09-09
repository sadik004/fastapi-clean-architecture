"""Cache Service encapsulating core asynchronous Redis data structures."""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable, Mapping
from typing import Annotated, Any, cast

from fastapi import Depends
from redis.asyncio import Redis

from app.core.dsa.xfetch import XFetchEnvelope, should_recompute
from app.core.redis import get_redis

logger = logging.getLogger(__name__)

METRICS_HITS_KEY: str = "metrics:cache:hits"
METRICS_MISSES_KEY: str = "metrics:cache:misses"
METRICS_XFETCH_HITS_KEY: str = "metrics:xfetch:normal_hits"
METRICS_XFETCH_EARLY_KEY: str = "metrics:xfetch:early_recomputations"
METRICS_XFETCH_MISSES_KEY: str = "metrics:xfetch:hard_misses"


class CacheService:
    """High-level service encapsulating asynchronous Redis data structure operations.

    Encapsulates raw Redis commands behind clean domain abstractions for:
      - Strings with TTL (Time-To-Live) and Atomic Counters
      - Hashes (Key-Value Field Dictionaries)
      - Lists (Double-Ended Queues / Buffers)
      - Sorted Sets (ZSET - Real-Time Rankings & Leaderboards)
    """

    def __init__(self, redis_client: Redis) -> None:
        self._redis = redis_client
        self._local_hits: int = 0
        self._local_misses: int = 0
        self._local_xfetch_hits: int = 0
        self._local_xfetch_early: int = 0
        self._local_xfetch_misses: int = 0

    # -------------------------------------------------------------------------
    # 1. String Operations with TTL
    # -------------------------------------------------------------------------

    async def set_str(self, key: str, value: str, expire_seconds: int | None = None) -> bool:
        """Store a string value in Redis with an optional expiration TTL in seconds.

        Complexity: O(1) time complexity.
        """
        result = await self._redis.set(name=key, value=value, ex=expire_seconds)
        return bool(result)

    async def get_str(self, key: str) -> str | None:
        """Retrieve a string value by key from Redis. Returns None if key does not exist.

        Complexity: O(1) time complexity.
        """
        value = await self._redis.get(name=key)
        if value is None:
            return None
        return str(value) if not isinstance(value, str) else value

    async def increment(self, key: str, amount: int = 1) -> int:
        """Atomically increment the integer value of a key by the given amount.

        Complexity: O(1) time complexity.
        """
        return int(await self._redis.incrby(name=key, amount=amount))

    # -------------------------------------------------------------------------
    # 2. Hash Operations (Dictionaries)
    # -------------------------------------------------------------------------

    async def hset_dict(self, key: str, mapping: Mapping[str, str]) -> int:
        """Set multiple hash fields to their respective values.

        Complexity: O(N) where N is the number of fields being set.
        """
        return int(await self._redis.hset(name=key, mapping=cast(Mapping[Any, Any], mapping)))

    async def hget_dict(self, key: str) -> dict[str, str]:
        """Retrieve all fields and values of the hash stored at key.

        Complexity: O(N) where N is the total size of the hash.
        """
        data = await self._redis.hgetall(name=key)
        if not data:
            return {}
        return {str(k): str(v) for k, v in data.items()}

    async def hget_field(self, key: str, field: str) -> str | None:
        """Retrieve the value of a specific hash field. Returns None if field or key is absent.

        Complexity: O(1) time complexity.
        """
        value = await self._redis.hget(name=key, key=field)
        if value is None:
            return None
        return str(value) if not isinstance(value, str) else value

    # -------------------------------------------------------------------------
    # 3. List Operations (Queues / Stacks)
    # -------------------------------------------------------------------------

    async def lpush_item(self, key: str, item: str) -> int:
        """Prepend an item to the head of a Redis list.

        Complexity: O(1) time complexity.
        """
        return int(await self._redis.lpush(key, item))

    async def rpop_item(self, key: str) -> str | None:
        """Remove and return the last element (tail) of a Redis list (FIFO queue pop).

        Complexity: O(1) time complexity.
        """
        value = await self._redis.rpop(name=key)
        if value is None:
            return None
        return str(value) if not isinstance(value, str) else value

    async def lrange_items(self, key: str, start: int = 0, stop: int = -1) -> list[str]:
        """Return the specified elements of the list stored at key.

        Complexity: O(S + N) where S is the offset and N is the number of elements.
        """
        items = await self._redis.lrange(name=key, start=start, end=stop)
        return [str(item) if not isinstance(item, str) else item for item in items]

    # -------------------------------------------------------------------------
    # Utility Operations
    # -------------------------------------------------------------------------

    async def delete(self, *keys: str) -> int:
        """Delete one or more keys from Redis.

        Complexity: O(N) where N is the number of keys.
        """
        if not keys:
            return 0
        return int(await self._redis.delete(*keys))

    async def exists(self, key: str) -> bool:
        """Check if a key exists in Redis.

        Complexity: O(1) time complexity.
        """
        return bool(await self._redis.exists(key))

    # -------------------------------------------------------------------------
    # 4. Sorted Set (ZSET) Operations
    # -------------------------------------------------------------------------

    async def zadd(self, key: str, mapping: Mapping[str, float]) -> int:
        """Add one or more members with scores to a sorted set, or update scores.

        Complexity: O(M * log(N)) where M is members added and N is total elements.
        """
        if not mapping:
            return 0
        return int(await self._redis.zadd(name=key, mapping=mapping))

    async def zincrby(self, key: str, amount: float, member: str) -> float:
        """Atomically increment the score of member in a sorted set by amount.

        Complexity: O(log(N)) time complexity.
        """
        result = await self._redis.zincrby(name=key, amount=amount, value=member)
        return float(result)

    async def zrevrank(self, key: str, member: str) -> int | None:
        """Return the 0-based rank of member ordered from highest score to lowest.

        Complexity: O(log(N)) time complexity.
        """
        rank = await self._redis.zrevrank(name=key, value=member)
        return int(rank) if rank is not None else None

    async def zscore(self, key: str, member: str) -> float | None:
        """Return the score of member in the sorted set.

        Complexity: O(1) time complexity via internal hash map.
        """
        score = await self._redis.zscore(name=key, value=member)
        return float(score) if score is not None else None

    async def zrevrange_with_scores(self, key: str, start: int, stop: int) -> list[tuple[str, float]]:
        """Return a slice of members and their scores ordered from highest to lowest.

        Complexity: O(log(N) + M) where M is the number of elements returned.
        """
        results = await self._redis.zrevrange(name=key, start=start, end=stop, withscores=True)
        return [(str(m) if not isinstance(m, str) else m, float(s)) for m, s in results]

    async def zrem(self, key: str, *members: str) -> int:
        """Remove one or more members from a sorted set.

        Complexity: O(M * log(N)) where M is the number of members removed.
        """
        if not members:
            return 0
        return int(await self._redis.zrem(key, *members))

    async def zcard(self, key: str) -> int:
        """Return the cardinality (number of members) of the sorted set.

        Complexity: O(1) time complexity.
        """
        return int(await self._redis.zcard(name=key))

    # -------------------------------------------------------------------------
    # Telemetry Operations
    # -------------------------------------------------------------------------

    async def record_hit(self) -> None:
        """Record a cache hit in Redis telemetry with in-memory fallback."""
        try:
            await self._redis.incrby(METRICS_HITS_KEY, 1)
        except Exception:
            self._local_hits += 1

    async def record_miss(self) -> None:
        """Record a cache miss in Redis telemetry with in-memory fallback."""
        try:
            await self._redis.incrby(METRICS_MISSES_KEY, 1)
        except Exception:
            self._local_misses += 1

    async def get_metrics(self) -> dict[str, Any]:
        """Calculate and return cache hit/miss counts and hit ratio.

        Complexity: O(1) time complexity.
        """
        hits = self._local_hits
        misses = self._local_misses
        try:
            raw_hits = await self._redis.get(METRICS_HITS_KEY)
            if raw_hits is not None:
                hits += int(raw_hits)
            raw_misses = await self._redis.get(METRICS_MISSES_KEY)
            if raw_misses is not None:
                misses += int(raw_misses)
        except Exception as exc:
            logger.debug("Redis metrics read failed: %s", exc)

        total = hits + misses
        ratio = round(hits / total, 4) if total > 0 else 0.0
        return {
            "hits": hits,
            "misses": misses,
            "hit_ratio": ratio,
        }

    async def reset_metrics(self) -> None:
        """Reset hit and miss telemetry counters."""
        try:
            await self._redis.delete(METRICS_HITS_KEY, METRICS_MISSES_KEY)
        except Exception as exc:
            logger.debug("Redis metrics reset failed: %s", exc)
        self._local_hits = 0
        self._local_misses = 0

    # -------------------------------------------------------------------------
    # 5. XFetch Probabilistic Cache Stampede Prevention
    # -------------------------------------------------------------------------

    async def xfetch_get_or_compute(
        self,
        key: str,
        compute_func: Callable[[], Awaitable[str]],
        ttl: float = 300.0,
        beta: float = 1.0,
        now: float | None = None,
        rand_val: float | None = None,
    ) -> str:
        """Fetch or recompute a value using the optimal probabilistic XFetch algorithm.

        Shields the database from Cache Stampedes (Thundering Herd) under high concurrency:
        1. Reads envelope from Redis (val, delta, expiry).
        2. If key doesn't exist (Hard Miss):
           - Measures delta computation time.
           - Executes compute_func().
           - Stores envelope in Redis with physical TTL padded by grace period:
             ttl + max(delta * 2, 10.0).
        3. If key exists:
           - Evaluates should_recompute(delta, expiry, now, beta, rand_val).
           - On False (Normal Hit): returns cached val immediately.
           - On True (Probabilistic Early Recomputation):
             - Exactly one lucky request initiates refresh before physical death.
             - Re-executes compute_func(), measures fresh delta, refreshes envelope in Redis.
             - Returns fresh val.

        Complexity: O(1) time complexity for cache check & math formula.
        """
        current_time = time.time() if now is None else now
        raw_envelope_str: str | None = None

        try:
            raw_envelope_str = await self.get_str(key)
        except Exception as exc:
            logger.warning("XFetch Redis read failed for key '%s': %s. Falling back to compute.", key, exc)

        if raw_envelope_str is not None:
            try:
                envelope = XFetchEnvelope.from_json(raw_envelope_str)
                recompute = should_recompute(
                    delta=envelope.delta,
                    expiry=envelope.expiry,
                    now=current_time,
                    beta=beta,
                    rand_val=rand_val,
                )
                if not recompute:
                    # Normal Hit: Serve warm data
                    await self._record_xfetch_hit()
                    return envelope.val

                # Probabilistic Early Recomputation: We are chosen to refresh early.
                # Optimistically bump logical expiry in Redis by max(envelope.delta * 2, 5.0) seconds
                # so concurrent requests in the next few milliseconds see a warm, valid cache and don't recompute.
                bump_lock_key = f"lock:{key}:recompute"
                acquired = False
                try:
                    # Non-blocking lock with short TTL (e.g. 10s)
                    acquired = bool(await self._redis.set(bump_lock_key, "1", nx=True, ex=10))
                except Exception:
                    acquired = True  # If Redis lock fails, proceed as normal

                if not acquired:
                    # Another concurrent worker is already actively recomputing.
                    # Serve the warm cached value immediately to avoid duplicate DB queries.
                    await self._record_xfetch_hit()
                    return envelope.val

                await self._record_xfetch_early()
            except Exception as exc:
                logger.warning("XFetch envelope parsing failed for key '%s': %s. Recomputing.", key, exc)
        else:
            # Hard Miss: Key not present in Redis
            await self._record_xfetch_miss()

        # Compute fresh value and measure execution duration
        start_t = time.perf_counter()
        try:
            fresh_val = await compute_func()
        finally:
            # Release recompute lock if it was acquired
            try:
                await self._redis.delete(f"lock:{key}:recompute")
            except Exception as exc:
                logger.debug("Failed to release recompute lock for key '%s': %s", key, exc)

        delta = max(time.perf_counter() - start_t, 0.001)

        logical_expiry = current_time + ttl
        physical_ttl = int(ttl + max(delta * 2.0, 10.0))

        fresh_envelope = XFetchEnvelope(
            val=fresh_val,
            delta=delta,
            expiry=logical_expiry,
        )

        try:
            await self.set_str(key, fresh_envelope.to_json(), expire_seconds=physical_ttl)
        except Exception as exc:
            logger.warning("XFetch Redis write failed for key '%s': %s.", key, exc)

        return fresh_val

    async def _record_xfetch_hit(self) -> None:
        """Record an XFetch normal hit."""
        try:
            await self._redis.incrby(METRICS_XFETCH_HITS_KEY, 1)
        except Exception:
            self._local_xfetch_hits += 1

    async def _record_xfetch_early(self) -> None:
        """Record an XFetch probabilistic early recomputation."""
        try:
            await self._redis.incrby(METRICS_XFETCH_EARLY_KEY, 1)
        except Exception:
            self._local_xfetch_early += 1

    async def _record_xfetch_miss(self) -> None:
        """Record an XFetch hard miss."""
        try:
            await self._redis.incrby(METRICS_XFETCH_MISSES_KEY, 1)
        except Exception:
            self._local_xfetch_misses += 1

    async def get_xfetch_metrics(self) -> dict[str, Any]:
        """Return real-time telemetry metrics for XFetch performance."""
        hits = self._local_xfetch_hits
        early = self._local_xfetch_early
        misses = self._local_xfetch_misses
        try:
            raw_hits = await self._redis.get(METRICS_XFETCH_HITS_KEY)
            if raw_hits is not None:
                hits += int(raw_hits)
            raw_early = await self._redis.get(METRICS_XFETCH_EARLY_KEY)
            if raw_early is not None:
                early += int(raw_early)
            raw_misses = await self._redis.get(METRICS_XFETCH_MISSES_KEY)
            if raw_misses is not None:
                misses += int(raw_misses)
        except Exception as exc:
            logger.debug("Redis XFetch metrics read failed: %s", exc)

        total = hits + early + misses
        return {
            "normal_hits": hits,
            "early_recomputations": early,
            "hard_misses": misses,
            "total_requests": total,
        }

    async def reset_xfetch_metrics(self) -> None:
        """Reset XFetch telemetry metrics in Redis and memory."""
        try:
            await self._redis.delete(
                METRICS_XFETCH_HITS_KEY,
                METRICS_XFETCH_EARLY_KEY,
                METRICS_XFETCH_MISSES_KEY,
            )
        except Exception as exc:
            logger.debug("Redis XFetch metrics reset failed: %s", exc)
        self._local_xfetch_hits = 0
        self._local_xfetch_early = 0
        self._local_xfetch_misses = 0


def get_cache_service(
    redis_client: Annotated[Redis, Depends(get_redis)],
) -> CacheService:
    """Dependency provider yielding an active CacheService instance."""
    return CacheService(redis_client=redis_client)
