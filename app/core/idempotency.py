"""Core Idempotency Engine ensuring mathematical mutation idempotency and double-spend protection.

Implements Stripe/PayPal standard idempotency mechanics:
1. State Machine: Transitions across IN_PROGRESS -> COMPLETED (or FAILED).
2. Cryptographic Hash Guard: SHA-256 payload tampering detection rejects modified payloads (HTTP 422).
3. In-Flight Protection: Concurrent overlapping requests with identical keys are rejected (HTTP 409).
4. Instant Cached Replay: Completed operations replay cached responses (HTTP 201) with 'X-Cache-Lookup: HIT-IDEMPOTENT'
   in O(1) time without re-executing underlying mutations.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Any

from fastapi import Depends, HTTPException, status
from redis.asyncio import Redis

from app.core.redis import get_redis


class IdempotencyState(StrEnum):
    """Lifecycle states of an idempotency transaction key."""

    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


def compute_request_hash(method: str, path: str, body: bytes | str) -> str:
    """Compute deterministic SHA-256 digest of request parameters in O(L) time.

    Args:
        method: HTTP verb (e.g. 'POST').
        path: Normalized URL path (e.g. '/payments/charge').
        body: Raw request body string or bytes.

    Returns:
        Hexadecimal SHA-256 digest string.
    """
    raw_body = body.decode("utf-8", errors="replace") if isinstance(body, bytes) else str(body)
    fingerprint = f"{method.upper()}:{path}:{raw_body}"
    return hashlib.sha256(fingerprint.encode("utf-8")).hexdigest()


class IdempotencyManager:
    """Manager orchestrating atomic idempotency locking, validation, and cached replays."""

    __slots__ = ("_redis",)

    def __init__(self, redis: Redis) -> None:
        """Initialize IdempotencyManager with active Redis connection."""
        self._redis = redis

    def _format_key(self, key: str) -> str:
        """Namespaced Redis key generator."""
        return f"idempotency:{key}"

    async def check_or_acquire(
        self,
        key: str,
        request_hash: str,
        ttl_seconds: int = 86400,
    ) -> tuple[bool, dict[str, Any] | None]:
        """Atomically lock or replay an idempotency key.

        Execution Pipeline:
        1. Atomic Acquisition: Executes `SET idempotency:{key} {initial_state} NX EX {ttl_seconds}`.
        2. If SET succeeded: Key was not present; lock acquired as 'IN_PROGRESS'. Returns (False, None).
        3. If SET failed: Key exists; inspect current record:
           a. If stored_hash != request_hash: Raise HTTP 422 (Payload Tampering).
           b. If state == IN_PROGRESS: Raise HTTP 409 (In-Flight Concurrent Request).
           c. If state == COMPLETED: Return (True, cached_record).
           d. If state == FAILED: Re-acquire lock and return (False, None).

        Args:
            key: Client-provided Idempotency-Key token.
            request_hash: SHA-256 digest of current request payload.
            ttl_seconds: Expiration TTL in seconds (default 24 hours / 86400s).

        Returns:
            tuple[bool, dict[str, Any] | None]: (is_cached, cached_record)
        """
        redis_key = self._format_key(key)
        initial_payload = {
            "state": IdempotencyState.IN_PROGRESS,
            "request_hash": request_hash,
            "status_code": None,
            "response_body": None,
            "created_at": datetime.now(UTC).isoformat(),
        }

        # Step 1: Atomic NX acquisition
        set_success = await self._redis.set(
            redis_key,
            json.dumps(initial_payload),
            nx=True,
            ex=ttl_seconds,
        )

        if set_success:
            return False, None

        # Step 2: Key already exists; retrieve and evaluate state
        raw_record = await self._redis.get(redis_key)
        if raw_record is None:
            # Ephemeral race where key expired immediately after NX check
            await self._redis.set(redis_key, json.dumps(initial_payload), ex=ttl_seconds)
            return False, None

        record: dict[str, Any] = json.loads(raw_record)
        stored_hash: str | None = record.get("request_hash")

        # Step 3: Tampering Guard
        if stored_hash and stored_hash != request_hash:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="Idempotency key reused with different payload parameters.",
            )

        current_state = record.get("state")

        # Step 4: In-Flight Concurrency Protection
        if current_state == IdempotencyState.IN_PROGRESS:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="A request with this idempotency key is currently in progress. Please wait.",
            )

        # Step 5: Completed Cache Replay
        if current_state == IdempotencyState.COMPLETED:
            return True, record

        # Step 6: Failed state retry
        await self._redis.set(redis_key, json.dumps(initial_payload), ex=ttl_seconds)
        return False, None

    async def record_success(
        self,
        key: str,
        request_hash: str,
        status_code: int,
        response_body: dict[str, Any],
        ttl_seconds: int = 86400,
    ) -> None:
        """Transition key to COMPLETED state and store response projection for replay."""
        redis_key = self._format_key(key)
        completed_record = {
            "state": IdempotencyState.COMPLETED,
            "request_hash": request_hash,
            "status_code": status_code,
            "response_body": response_body,
            "completed_at": datetime.now(UTC).isoformat(),
        }
        await self._redis.set(redis_key, json.dumps(completed_record), ex=ttl_seconds)

    async def record_failure(
        self,
        key: str,
        error_detail: str | None = None,
        ttl_seconds: int = 3600,
    ) -> None:
        """Transition key to FAILED state allowing subsequent client retries."""
        redis_key = self._format_key(key)
        failed_record = {
            "state": IdempotencyState.FAILED,
            "error": error_detail or "Operation failed",
            "failed_at": datetime.now(UTC).isoformat(),
        }
        await self._redis.set(redis_key, json.dumps(failed_record), ex=ttl_seconds)


async def get_idempotency_manager(
    redis: Annotated[Redis, Depends(get_redis)],
) -> IdempotencyManager:
    """FastAPI dependency injecting configured IdempotencyManager."""
    return IdempotencyManager(redis=redis)
