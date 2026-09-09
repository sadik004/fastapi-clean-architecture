"""Cache Service encapsulating core asynchronous Redis data structures."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Annotated, Any, cast

from fastapi import Depends
from redis.asyncio import Redis

from app.core.redis import get_redis


class CacheService:
    """High-level service encapsulating asynchronous Redis data structure operations.

    Encapsulates raw Redis commands behind clean domain abstractions for:
      - Strings with TTL (Time-To-Live) and Atomic Counters
      - Hashes (Key-Value Field Dictionaries)
      - Lists (Double-Ended Queues / Buffers)
    """

    def __init__(self, redis_client: Redis) -> None:
        self._redis = redis_client

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


def get_cache_service(
    redis_client: Annotated[Redis, Depends(get_redis)],
) -> CacheService:
    """Dependency provider yielding an active CacheService instance."""
    return CacheService(redis_client=redis_client)
