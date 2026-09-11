"""Multi-Tier Graceful Degradation and Fallback Engine (Netflix/High-Scale SRE Standard).

Provides progressive step-down degradation for auxiliary services:
Tier 1 (Primary Live Call) -> Tier 2 (Stale Redis Cache) -> Tier 3 (Static Safe Default)

Eliminates HTTP 500 errors on client-facing views when non-critical downstream dependencies fail.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from enum import Enum
from typing import Any, TypeVar

from redis.asyncio import Redis

logger = logging.getLogger("app.resilience.fallback")

T = TypeVar("T")


class DegradationLevel(str, Enum):
    """Execution status and degradation tier for served response data."""

    PRIMARY = "PRIMARY"
    STALE_CACHE = "STALE_CACHE"
    STATIC_DEFAULT = "STATIC_DEFAULT"


class FallbackEngine:
    """Enterprise multi-tier fallback engine orchestrating graceful degradation."""

    __slots__ = ("_redis_client",)

    def __init__(self, redis_client: Redis | None = None) -> None:
        self._redis_client = redis_client

    async def _get_redis(self) -> Redis | None:
        """Resolve the active Redis client, lazily initializing if required."""
        if self._redis_client is not None:
            return self._redis_client

        try:
            import app.core.redis as redis_mod

            client = redis_mod._redis_client
            if client is not None:
                if redis_mod._is_fallback:
                    current_loop = asyncio.get_running_loop()
                    conn = getattr(client, "connection", None)
                    if conn is not None:
                        sock = getattr(conn, "_socket", None)
                        if sock is not None and hasattr(sock, "responses"):
                            queue_loop = getattr(sock.responses, "_loop", None)
                            if queue_loop is not None and (queue_loop.is_closed() or queue_loop is not current_loop):
                                await redis_mod.init_redis_pool()
                                return redis_mod._redis_client
                return client

            await redis_mod.init_redis_pool()
            return redis_mod._redis_client
        except Exception as exc:
            logger.warning("Redis client resolution failed in FallbackEngine: %s", exc)
            return None

    async def _safe_cache_set(
        self,
        key: str,
        value: Any,
        ttl: int,
        serialize: Callable[[Any], str] | None = None,
    ) -> None:
        """Asynchronously update fallback cache; errors are intercepted to prevent primary disruption."""
        try:
            client = await self._get_redis()
            if client is None:
                return

            payload = serialize(value) if serialize is not None else json.dumps(value, default=str)
            await client.set(key, payload, ex=ttl)
        except Exception as exc:
            logger.warning("Failed to asynchronously update fallback cache for key '%s': %s", key, exc)

    async def _safe_cache_get(
        self,
        key: str,
        deserialize: Callable[[str], T] | None = None,
    ) -> T | None:
        """Safely fetch and deserialize cached fallback data; errors return None without raising."""
        try:
            client = await self._get_redis()
            if client is None:
                return None

            raw = await client.get(key)
            if raw is None:
                return None

            if isinstance(raw, bytes):
                raw = raw.decode("utf-8")

            if deserialize is not None:
                return deserialize(raw)

            data: T = json.loads(raw)
            return data
        except Exception as exc:
            logger.warning("Failed to fetch stale fallback cache for key '%s': %s", key, exc)
            return None

    async def execute_with_fallback(
        self,
        primary_func: Callable[[], Awaitable[T]],
        fallback_cache_key: str | None,
        static_default: T,
        cache_ttl: int = 3600,
        serialize: Callable[[T], str] | None = None,
        deserialize: Callable[[str], T] | None = None,
    ) -> tuple[T, DegradationLevel]:
        """Execute operation with progressive multi-tier fallback step-down.

        Tiers:
        1. Tier 1 (Primary): Await primary_func(). If successful, update cache and return PRIMARY.
        2. Tier 2 (Stale Cache): If primary_func fails, check Redis for fallback_cache_key.
           If found, return STALE_CACHE.
        3. Tier 3 (Static Default): If cache is empty or Redis is down, return STATIC_DEFAULT.
           Never raises an unhandled HTTP 500 error!
        """
        # ----------------------------------------------------------------------
        # Tier 1: Primary Live Execution
        # ----------------------------------------------------------------------
        try:
            result = await primary_func()
            if fallback_cache_key is not None:
                await self._safe_cache_set(fallback_cache_key, result, cache_ttl, serialize)
            return result, DegradationLevel.PRIMARY
        except Exception as primary_exc:
            logger.warning(
                "Primary operation failed (%s: %s). Stepping down to Tier 2 (Stale Cache)...",
                type(primary_exc).__name__,
                primary_exc,
            )

        # ----------------------------------------------------------------------
        # Tier 2: Stale Cache Fallback
        # ----------------------------------------------------------------------
        if fallback_cache_key is not None:
            stale_data = await self._safe_cache_get(fallback_cache_key, deserialize)
            if stale_data is not None:
                logger.info(
                    "Gracefully degraded to Tier 2 (Stale Cache) for key '%s'.",
                    fallback_cache_key,
                )
                return stale_data, DegradationLevel.STALE_CACHE

        # ----------------------------------------------------------------------
        # Tier 3: Static Safe Default Fallback
        # ----------------------------------------------------------------------
        logger.warning(
            "Stale cache unavailable for key '%s'. Gracefully degraded to Tier 3 (Static Default).",
            fallback_cache_key,
        )
        return static_default, DegradationLevel.STATIC_DEFAULT


__all__ = ["DegradationLevel", "FallbackEngine"]
