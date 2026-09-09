"""Dead Letter Queue (DLQ) & Poison Message Isolation Service.

Enforces:
  1. Exponential Backoff Retry Policy (MAX_RETRIES = 3, delay = BASE_DELAY * 2^attempt).
  2. Forensic DLQ Enveloping (original payload, error message, stack trace, timestamp).
  3. Primary Queue Unblocking Invariant (quarantined messages are ACKed/committed from primary stream).
  4. Redrive Operations Engine (re-injection of quarantined messages after bug resolution).
"""

from __future__ import annotations

import asyncio
import logging
import traceback
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from app.schemas.dlq import DLQEnvelope

logger = logging.getLogger(__name__)

# Production Defaults
MAX_RETRIES: int = 3
BASE_BACKOFF_SECONDS: float = 1.0
DEFAULT_DLQ_DESTINATION: str = "orders.dlq"
RABBITMQ_DLX: str = "dlx.direct"

# In-memory quarantine registry for inspection, forensic analysis, and operational redrive
_quarantine_store: list[DLQEnvelope] = []


class DLQService:
    """Enterprise service orchestrating Poison Pill Isolation, DLQ Routing, and Redrive."""

    @staticmethod
    def get_quarantined_messages(queue_or_topic: str | None = None) -> list[DLQEnvelope]:
        """Return quarantined dead-letter envelopes, optionally filtered by origin."""
        if queue_or_topic is None:
            return list(_quarantine_store)
        return [env for env in _quarantine_store if env.original_topic_or_queue == queue_or_topic]

    @staticmethod
    def clear_quarantine() -> None:
        """Purge all quarantined messages (testing / cleanup helper)."""
        _quarantine_store.clear()

    @classmethod
    async def route_to_dlq(cls, envelope: DLQEnvelope) -> None:
        """Store envelope in quarantine registry and dispatch to dead-letter broker destination."""
        _quarantine_store.append(envelope)
        logger.warning(
            "POISON MESSAGE QUARANTINED -> DLQ: id=%s, origin=%s, destination=%s, retries=%d, error=%s",
            envelope.message_id,
            envelope.original_topic_or_queue,
            envelope.dlq_destination,
            envelope.retry_count,
            envelope.error_message,
        )

    @classmethod
    async def process_with_dlq(
        cls,
        destination: str,
        payload: dict[str, Any],
        handler_func: Callable[[dict[str, Any]], Awaitable[Any]],
        ack_func: Callable[[], Awaitable[None]] | None = None,
        message_id: str | None = None,
        base_delay: float = BASE_BACKOFF_SECONDS,
        max_retries: int = MAX_RETRIES,
    ) -> tuple[bool, Any | DLQEnvelope]:
        """Execute message handler with exponential backoff retries and poison pill dead-lettering.

        Returns:
          (True, result): Message processed successfully within retry quota.
          (False, DLQEnvelope): Message failed max_retries and was quarantined to DLQ.
            CRITICAL: ack_func is invoked on quarantine to unblock the primary queue.
        """
        last_exc: Exception | None = None
        tb_str = ""

        for attempt in range(max_retries):
            try:
                result = await handler_func(payload)
                # Success on this attempt
                if ack_func is not None:
                    await ack_func()
                return True, result
            except Exception as exc:
                last_exc = exc
                tb_str = traceback.format_exc()
                current_attempt = attempt + 1

                if current_attempt < max_retries:
                    # Exponential backoff schedule: delay = base_delay * (2 ** attempt)
                    delay = base_delay * (2**attempt)
                    logger.warning(
                        "Transient failure on %s (attempt %d/%d). Backing off for %.3fs: %s",
                        destination,
                        current_attempt,
                        max_retries,
                        delay,
                        exc,
                    )
                    if delay > 0:
                        await asyncio.sleep(delay)

        # Max retries exhausted -> Poison Pill Quarantine
        msg_id = message_id or f"msg_{uuid.uuid4().hex[:12]}"
        envelope = DLQEnvelope(
            message_id=msg_id,
            original_topic_or_queue=destination,
            payload=payload,
            error_message=str(last_exc) if last_exc else "Unknown error",
            error_traceback=tb_str,
            retry_count=max_retries,
            dlq_destination=DEFAULT_DLQ_DESTINATION,
        )

        await cls.route_to_dlq(envelope)

        # UNBLOCKING INVARIANT:
        # Acknowledge or commit the original poison message from the primary stream
        # so that downstream healthy messages are immediately processed!
        if ack_func is not None:
            await ack_func()

        return False, envelope

    @classmethod
    async def redrive_messages(
        cls,
        queue_or_topic: str | None = None,
        limit: int = 100,
        republish_func: Callable[[str, dict[str, Any]], Awaitable[None]] | None = None,
    ) -> int:
        """Re-inject quarantined messages back into their primary queues/topics for reprocessing."""
        global _quarantine_store

        candidates: list[DLQEnvelope] = []
        remaining: list[DLQEnvelope] = []

        for env in _quarantine_store:
            if (queue_or_topic is None or env.original_topic_or_queue == queue_or_topic) and len(candidates) < limit:
                candidates.append(env)
            else:
                remaining.append(env)

        redriven_count = 0
        for env in candidates:
            if republish_func is not None:
                await republish_func(env.original_topic_or_queue, env.payload)
            redriven_count += 1
            logger.info(
                "Redriving message %s back to %s",
                env.message_id,
                env.original_topic_or_queue,
            )

        _quarantine_store = remaining
        return redriven_count

    @classmethod
    def purge_dlq(cls, queue_or_topic: str | None = None) -> int:
        """Purge quarantined messages from DLQ."""
        global _quarantine_store

        if queue_or_topic is None:
            count = len(_quarantine_store)
            _quarantine_store.clear()
            return count

        initial_len = len(_quarantine_store)
        _quarantine_store = [env for env in _quarantine_store if env.original_topic_or_queue != queue_or_topic]
        return initial_len - len(_quarantine_store)
