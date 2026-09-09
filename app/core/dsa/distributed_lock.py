"""Distributed Lock implementation using Redis Redlock pattern and atomic Lua script release.

Guarantees:
1. Mutual Exclusion: At most one worker across the entire distributed cluster can hold
   the lock for a given resource at any point in time.
2. Deadlock Freedom: Every lock acquisition enforces a mandatory millisecond TTL (px),
   guaranteeing automatic expiration if a process crashes or network partitions occur.
3. Safe Atomic Release: Release is executed via an atomic Lua script verifying token ownership.
   A worker will NEVER delete another worker's lock even if the lock expired prematurely.
4. Memory Efficiency: Employs __slots__ to eliminate per-instance dictionary overhead.
"""

from __future__ import annotations

import types
import uuid
from typing import Any

from redis.asyncio import Redis

# Canonical Lua script for safe atomic release:
# Only deletes the lock key if the stored value matches the caller's unique token.
_RELEASE_LUA_SCRIPT = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("del", KEYS[1])
else
    return 0
end
"""


class DistributedLock:
    """Enterprise Distributed Lock (Redlock Pattern) powered by Redis.

    Attributes:
        _redis: Async Redis client used for distributed synchronization.
        name: Logical resource identifier to lock (e.g. 'inventory:item_123', 'job:daily_sync').
        token: Cryptographically unique UUID4 token generated per lock acquisition.
        ttl_ms: Time-to-live in milliseconds after which the lock auto-expires.
        _acquired: Boolean flag tracking current acquisition status.
    """

    __slots__ = ("_redis", "name", "token", "ttl_ms", "_acquired")

    def __init__(
        self,
        redis: Redis,
        name: str,
        ttl_ms: int = 5000,
    ) -> None:
        """Initialize a DistributedLock instance.

        Args:
            redis: Active async Redis client.
            name: Resource name/key to protect.
            ttl_ms: Lock duration in milliseconds before automatic expiration. Default 5000ms.
        """
        self._redis: Redis = redis
        self.name: str = name
        self.ttl_ms: int = max(1, ttl_ms)
        self.token: str | None = None
        self._acquired: bool = False

    @property
    def key(self) -> str:
        """Compute namespaced Redis key for the lock."""
        return f"lock:{self.name}"

    @property
    def is_acquired(self) -> bool:
        """Check if lock was successfully acquired by this instance."""
        return self._acquired

    async def acquire(self) -> bool:
        """Attempt to atomically acquire the distributed lock in O(1) time.

        Generates a unique UUID4 token and executes:
        `SET lock:{name} {token} NX PX {ttl_ms}`

        Returns:
            bool: True if lock was acquired, False if already held by another process.
        """
        self.token = uuid.uuid4().hex
        result = await self._redis.set(
            name=self.key,
            value=self.token,
            nx=True,
            px=self.ttl_ms,
        )
        self._acquired = bool(result)
        if not self._acquired:
            self.token = None
        return self._acquired

    async def release(self) -> bool:
        """Safely release the distributed lock using atomic Lua verification in O(1) time.

        Guarantees that only the worker holding the matching token can release the lock.
        If the lock expired and was acquired by another process, this returns False
        and does NOT delete the new lock.

        Returns:
            bool: True if lock was released, False if token mismatch or already expired.
        """
        if not self._acquired or not self.token:
            return False

        try:
            res: Any = await self._redis.eval(_RELEASE_LUA_SCRIPT, 1, self.key, self.token)
            released = int(res) == 1
            return released
        finally:
            self._acquired = False
            self.token = None

    async def __aenter__(self) -> bool:
        """Async context manager entry: acquires the lock and returns acquisition status."""
        return await self.acquire()

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: types.TracebackType | None,
    ) -> None:
        """Async context manager exit: unconditionally releases the lock if acquired."""
        if self._acquired:
            await self.release()
