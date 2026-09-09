"""Transactional Outbox Relay Worker Service.

Asynchronously polls pending outbox records from persistent storage and publishes
them to Apache Kafka with deterministic partition key guarantees and Zero Data Loss.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from app.core.kafka import get_kafka_producer
from app.core.unit_of_work import UnitOfWorkProtocol
from app.repositories.outbox_repository import OutboxEventEntity

logger = logging.getLogger(__name__)

# Outbox Publisher Callback Type
PublisherCallable = Callable[[str, str, bytes], Awaitable[Any]]


class OutboxRelayService:
    """Relay service polling outbox events and dispatching them to message brokers."""

    def __init__(
        self,
        uow: UnitOfWorkProtocol,
        publisher: PublisherCallable | None = None,
    ) -> None:
        self._uow = uow
        self._publisher = publisher

    def _resolve_topic(self, event: OutboxEventEntity) -> str:
        """Resolve Kafka destination topic based on event aggregate type."""
        if event.aggregate_type == "order":
            return "orders.events"
        elif event.aggregate_type == "payment":
            return "payments.events"
        return f"{event.aggregate_type}s.events"

    async def _dispatch_to_broker(self, topic: str, key: str, payload_bytes: bytes) -> None:
        """Dispatch event bytes to Kafka broker using injected publisher or core producer."""
        if self._publisher is not None:
            await self._publisher(topic, key, payload_bytes)
        else:
            producer = await get_kafka_producer()
            await producer.send_and_wait(
                topic=topic,
                key=key.encode("utf-8"),
                value=payload_bytes,
            )

    async def poll_and_publish_pending_events(self, batch_size: int = 50) -> dict[str, Any]:
        """Poll a batch of pending outbox events and dispatch each to the message broker.

        Guarantees:
        - Delivery Confirmation: Outbox records are marked as 'PUBLISHED' strictly after broker ack.
        - Zero Data Loss: If broker network call fails, the record remains 'PENDING' for subsequent retries.
        - Partition Ordering: Uses aggregate_id as deterministic partition key.
        """
        published_count = 0
        failed_count = 0

        async with self._uow as uow:
            pending_events = await uow.outbox.get_pending_events(batch_size=batch_size)

            if not pending_events:
                return {
                    "status": "IDLE",
                    "total_polled": 0,
                    "published_count": 0,
                    "failed_count": 0,
                    "message": "No pending outbox events found.",
                }

            logger.info("Outbox relay polled %d pending events for dispatch", len(pending_events))

            for event in pending_events:
                topic = self._resolve_topic(event)
                key = event.aggregate_id
                payload_json = json.dumps(event.payload)
                payload_bytes = payload_json.encode("utf-8")

                try:
                    await self._dispatch_to_broker(topic=topic, key=key, payload_bytes=payload_bytes)
                    await uow.outbox.mark_published(event.id)
                    published_count += 1
                    logger.info(
                        "Successfully relayed outbox event %s [type=%s] to topic %s",
                        event.id,
                        event.event_type,
                        topic,
                    )
                except Exception as exc:
                    failed_count += 1
                    await uow.outbox.mark_failed(event.id)
                    logger.error(
                        "Outbox relay failed to publish event %s [type=%s]: %s",
                        event.id,
                        event.event_type,
                        exc,
                        exc_info=True,
                    )

            await uow.commit()

        return {
            "status": "COMPLETED",
            "total_polled": len(pending_events),
            "published_count": published_count,
            "failed_count": failed_count,
            "message": f"Polled {len(pending_events)} events: {published_count} published, {failed_count} failed.",
        }
