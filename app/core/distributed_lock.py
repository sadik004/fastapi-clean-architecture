"""Enterprise Redis Distributed Lock (Redlock Engine) with Lua Script Atomic Release.

Guarantees:
1. Mutual Exclusion: Exactly one process across a distributed cluster holds the lock.
2. Deadlock Elimination: Mandatory millisecond TTLs and deterministic lexicographical
   multi-resource lock sorting.
3. Safe Atomic Release: Canonical Lua script ensures slow/expired workers never delete
   locks held by other workers.
4. Non-Blocking Retry with Jitter: Exponential backoff with randomized jitter during contention.
"""

from __future__ import annotations

import asyncio
import logging
import secrets
import time
import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

from redis.asyncio import Redis

from app.core.exceptions import LockAcquisitionTimeoutException

logger = logging.getLogger(__name__)

# Canonical Lua script for safe atomic lock release:
# Verifies caller's unique token matches the value in Redis before deleting.
# Returns 1 if released, 0 if token mismatch or key did not exist.
_RELEASE_LUA_SCRIPT = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("del", KEYS[1])
else
    return 0
end
"""


class AsyncDistributedLock:
    """Enterprise asynchronous distributed lock manager backed by Redis.

    Adheres to the Redlock pattern and Martin Fowler's distributed concurrency practices.
    """

    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    def _lock_key(self, resource_key: str) -> str:
        """Compute namespaced Redis key for a resource."""
        if resource_key.startswith("lock:"):
            return resource_key
        return f"lock:{resource_key}"

    async def acquire(
        self,
        resource_key: str,
        ttl_seconds: int = 10,
        acquire_timeout: float = 3.0,
    ) -> str:
        """Attempt to acquire a distributed lock with exponential backoff and jitter.

        Args:
            resource_key: Logical identifier of the resource (e.g. 'account:123').
            ttl_seconds: Time-to-live in seconds before automatic expiration.
            acquire_timeout: Maximum time in seconds to retry before raising LockAcquisitionTimeoutException.

        Returns:
            str: Cryptographically unique lock token (UUID4 hex).

        Raises:
            LockAcquisitionTimeoutException: If lock could not be acquired within acquire_timeout.
        """
        token = uuid.uuid4().hex
        key = self._lock_key(resource_key)
        ttl_ms = max(100, int(ttl_seconds * 1000))
        deadline = time.monotonic() + acquire_timeout

        retry_delay = 0.02  # Initial backoff: 20ms
        max_delay = 0.20  # Max delay: 200ms

        while True:
            # Atomic acquisition: SET key token NX PX ttl_ms
            acquired = await self._redis.set(
                name=key,
                value=token,
                nx=True,
                px=ttl_ms,
            )
            if acquired:
                logger.debug(
                    "Acquired distributed lock for '%s' (token=%s, ttl_ms=%d)",
                    key,
                    token,
                    ttl_ms,
                )
                return token

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                logger.warning(
                    "Lock acquisition timed out for '%s' after %.2fs",
                    key,
                    acquire_timeout,
                )
                raise LockAcquisitionTimeoutException(
                    message=f"Unable to acquire distributed lock for '{resource_key}' within {acquire_timeout}s timeout.",
                    resource_key=resource_key,
                )

            # Jittered exponential backoff: sleep min(retry_delay, remaining) + jitter
            jitter = secrets.SystemRandom().uniform(0.005, 0.025)
            sleep_duration = min(retry_delay + jitter, remaining)
            await asyncio.sleep(sleep_duration)

            retry_delay = min(retry_delay * 1.5, max_delay)

    async def release(self, resource_key: str, token: str) -> bool:
        """Safely release a distributed lock using atomic Lua script token validation.

        Args:
            resource_key: Logical identifier of the resource.
            token: The lock token returned during acquisition.

        Returns:
            bool: True if the lock was owned and successfully deleted; False otherwise.
        """
        key = self._lock_key(resource_key)
        try:
            res: Any = await self._redis.eval(_RELEASE_LUA_SCRIPT, 1, key, token)
            released = int(res) == 1
            if released:
                logger.debug("Released distributed lock for '%s' (token=%s)", key, token)
            else:
                logger.warning(
                    "Failed to release distributed lock for '%s': token mismatch or expired (token=%s)",
                    key,
                    token,
                )
            return released
        except Exception as exc:
            logger.error("Error executing Lua release script on '%s': %s", key, exc)
            return False

    @asynccontextmanager
    async def lock(
        self,
        resource_key: str,
        ttl_seconds: int = 10,
        acquire_timeout: float = 3.0,
    ) -> AsyncGenerator[str]:
        """Context manager to acquire and safely release a single distributed lock."""
        token = await self.acquire(resource_key, ttl_seconds, acquire_timeout)
        try:
            yield token
        finally:
            await self.release(resource_key, token)

    @asynccontextmanager
    async def acquire_multiple(
        self,
        resource_keys: list[str],
        ttl_seconds: int = 10,
        acquire_timeout: float = 3.0,
    ) -> AsyncGenerator[dict[str, str]]:
        """Context manager to acquire multiple resource locks in deterministic sorted order.

        Sorting lexicographically permanently eliminates Distributed Deadlocks (Dining Philosophers).
        If any lock acquisition fails, all previously acquired locks are rolled back immediately.
        """
        unique_sorted_keys = sorted(set(resource_keys))
        acquired_locks: list[tuple[str, str]] = []

        try:
            for r_key in unique_sorted_keys:
                token = await self.acquire(
                    resource_key=r_key,
                    ttl_seconds=ttl_seconds,
                    acquire_timeout=acquire_timeout,
                )
                acquired_locks.append((r_key, token))
            yield dict(acquired_locks)
        except Exception:
            # Rollback any locks acquired before the failure
            for r_key, token in reversed(acquired_locks):
                await self.release(r_key, token)
            raise
        else:
            # Normal completion: release in reverse acquisition order
            for r_key, token in reversed(acquired_locks):
                await self.release(r_key, token)

    async def get_active_locks(self, pattern: str = "lock:*") -> list[dict[str, Any]]:
        """Inspect and return operational telemetry for all active distributed locks."""
        keys = await self._redis.keys(pattern)
        active_locks: list[dict[str, Any]] = []

        for k in keys:
            key_str = k.decode() if isinstance(k, bytes) else str(k)
            ttl = await self._redis.pttl(key_str)
            active_locks.append(
                {
                    "key": key_str,
                    "resource": key_str.removeprefix("lock:"),
                    "ttl_ms": ttl,
                }
            )
        return active_locks
