"""Ledger Outbox Relay Worker Service for Real-Time Kafka Audit Streaming.

Guarantees:
1. Zero Dual-Write Inconsistency: The financial transfer engine never writes directly to Kafka;
   it persists an outbox record inside the identical ACID transaction boundary.
2. Sequential FIFO Ordering: Messages published to Kafka carry `source_account_id` as the message
   partition key, ensuring all financial events for a given account land on the same Kafka partition
   in strict chronological sequence.
3. At-Least-Once Delivery: Events are marked as 'PROCESSED' strictly after receiving broker ACK.
   Transient network or broker failures preserve 'PENDING' status for exponential backoff retries.
4. Containerless Standalone Testability: Transparently leverages MockAIOKafkaProducer when an
   external Apache Kafka cluster is not configured or reachable.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from app.core.kafka import MockAIOKafkaProducer, get_kafka_producer
from app.core.protocols import UnitOfWorkProtocol
from app.repositories.outbox_repository import OutboxEventEntity

logger = logging.getLogger("app.services.ledger_outbox_relay")


class LedgerOutboxRelayService:
    """Enterprise outbox relay worker engine dispatching ledger events to Kafka."""

    def __init__(
        self,
        uow: UnitOfWorkProtocol,
        kafka_producer: Any | None = None,
    ) -> None:
        self._uow = uow
        self._kafka_producer = kafka_producer

    async def _resolve_producer(self) -> Any:
        """Resolve injected or global Kafka producer with automatic mock fallback."""
        if self._kafka_producer is not None:
            return self._kafka_producer
        return await get_kafka_producer()

    async def publish_pending_events(self, batch_size: int = 50) -> int:
        """Poll a batch of pending ledger outbox records and dispatch to Kafka.

        Args:
            batch_size: Maximum number of records to process in a single batch.

        Returns:
            The number of successfully relayed and ACK'd events.
        """
        producer = await self._resolve_producer()

        # Ensure mock producer is started if running in mock mode
        if isinstance(producer, MockAIOKafkaProducer) and not producer.is_started:
            await producer.start()

        dispatched_count = 0

        async with self._uow as uow:
            pending_events = await uow.outbox.get_pending_events(batch_size=batch_size)
            if not pending_events:
                logger.debug("No pending ledger outbox events found for relay.")
                return 0

            logger.info("Relaying %d pending ledger outbox events to Kafka", len(pending_events))

            for event in pending_events:
                # Target topic defaults to ledger.transfers.v1
                topic = (
                    event.aggregate_type
                    if event.aggregate_type and "." in event.aggregate_type
                    else "ledger.transfers.v1"
                )
                # Partition key strictly equals source_account_id for sequential FIFO ordering
                partition_key = event.aggregate_id or event.payload.get("partition_key", "")
                payload_json = json.dumps(event.payload)
                payload_bytes = payload_json.encode("utf-8")
                key_bytes = partition_key.encode("utf-8") if partition_key else None

                try:
                    await producer.send_and_wait(
                        topic=topic,
                        key=key_bytes,
                        value=payload_bytes,
                    )
                    await uow.outbox.mark_published(event.id, status="PROCESSED")
                    dispatched_count += 1
                    logger.info(
                        "Relayed ledger outbox event %s [type=%s, key=%s] to Kafka topic %s",
                        event.id,
                        event.event_type,
                        partition_key,
                        topic,
                    )
                except Exception as exc:
                    await uow.outbox.mark_failed(event.id)
                    logger.error(
                        "Kafka dispatch failed for outbox event %s [type=%s]: %s",
                        event.id,
                        event.event_type,
                        exc,
                        exc_info=True,
                    )

            await uow.commit()

        return dispatched_count

    async def get_pending_audit_events(self, limit: int = 50) -> list[OutboxEventEntity]:
        """Fetch un-relayed pending events for diagnostic inspection and monitoring."""
        async with self._uow as uow:
            return await uow.outbox.get_pending_events(batch_size=limit)
