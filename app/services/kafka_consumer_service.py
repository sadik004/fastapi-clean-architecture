"""Apache Kafka Consumer Service Layer with Manual Post-Commit Durability.

Encapsulates safe Kafka stream consumption loops:
  1. Polls bounded batches from assigned topic partitions.
  2. Deserializes and validates CloudEvent payloads.
  3. Executes domain logic / DB mutations.
  4. Manually commits offsets strictly AFTER domain logic succeeds.
  5. Computes real-time partition lag telemetry.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from app.core.kafka_consumer import create_kafka_consumer
from app.schemas.kafka_events import (
    KafkaConsumerGroupStatus,
    KafkaPartitionLag,
    KafkaPollBatchResponse,
    OrderCreatedEvent,
    PaymentProcessedEvent,
)

logger = logging.getLogger(__name__)


class KafkaConsumerService:
    """Service orchestrating concurrent Kafka consumer groups and manual offset commits."""

    @staticmethod
    async def consume_batch(
        topic: str = "orders.events",
        group_id: str = "order-processing-group",
        batch_size: int = 10,
        process_callback: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
        force_mock: bool = False,
    ) -> KafkaPollBatchResponse:
        """Poll a bounded batch of records, execute domain processing, and manually commit offsets.

        CRITICAL DATA LOSS PREVENTION:
          Offsets are committed only AFTER all records in the batch (or up to the failure point)
          have been successfully processed. If an exception occurs, uncommitted offsets
          are preserved so records are re-read on subsequent polls.
        """
        consumer = await create_kafka_consumer(
            topic,
            group_id=group_id,
            max_poll_records=batch_size,
            force_mock=force_mock,
        )

        await consumer.start()
        processed_events: list[dict[str, Any]] = []
        committed_offsets_map: dict[int, int] = {}

        try:
            # Poll messages up to batch_size
            record_map = await consumer.getmany(timeout_ms=100, max_records=batch_size)
            offsets_to_commit: dict[Any, int] = {}

            for tp, records in record_map.items():
                for record in records:
                    raw_val = record.value.decode("utf-8") if isinstance(record.value, bytes) else str(record.value)
                    try:
                        data = json.loads(raw_val)
                    except Exception:
                        data = {"raw": raw_val}

                    # Validate CloudEvents schema contract when matching known types
                    event_type = data.get("event_type")
                    if event_type == "order.created":
                        OrderCreatedEvent.model_validate(data)
                    elif event_type == "payment.processed":
                        PaymentProcessedEvent.model_validate(data)

                    # Execute domain mutation / business logic
                    if process_callback is not None:
                        await process_callback(data)

                    processed_events.append(data)
                    # Next offset to fetch in Kafka convention is current offset + 1
                    next_offset = record.offset + 1
                    offsets_to_commit[tp] = next_offset
                    committed_offsets_map[tp.partition] = next_offset

            # Post-Processing Manual Commit
            if offsets_to_commit:
                await consumer.commit(offsets_to_commit)
                logger.info(
                    "Consumer group %s committed offsets: %s",
                    group_id,
                    committed_offsets_map,
                )

            return KafkaPollBatchResponse(
                topic=topic,
                group_id=group_id,
                records_processed=len(processed_events),
                committed_offsets=committed_offsets_map,
                events=processed_events,
                message=(
                    f"Successfully processed {len(processed_events)} records and committed offsets"
                    if processed_events
                    else "No new records available in topic"
                ),
            )
        finally:
            await consumer.stop()

    @staticmethod
    async def get_consumer_group_status(
        group_id: str,
        topic: str = "orders.events",
        force_mock: bool = False,
    ) -> KafkaConsumerGroupStatus:
        """Query consumer group health, assigned partitions, current offsets, and lag."""
        consumer = await create_kafka_consumer(
            topic,
            group_id=group_id,
            force_mock=force_mock,
        )

        await consumer.start()
        try:
            assigned = consumer.assignment()
            partition_lags: list[KafkaPartitionLag] = []
            assigned_indices: list[int] = []
            total_lag = 0

            sorted_partitions = sorted(assigned, key=lambda x: x.partition)
            for tp in sorted_partitions:
                assigned_indices.append(tp.partition)
                committed_val = await consumer.committed(tp)
                current_offset = committed_val if committed_val is not None else 0

                end_offset = consumer.highwater(tp) if hasattr(consumer, "highwater") else 0
                lag = max(0, end_offset - current_offset)
                total_lag += lag

                partition_lags.append(
                    KafkaPartitionLag(
                        partition=tp.partition,
                        current_offset=current_offset,
                        end_offset=end_offset,
                        lag=lag,
                    )
                )

            return KafkaConsumerGroupStatus(
                group_id=group_id,
                topic=topic,
                state="STABLE",
                assigned_partitions=assigned_indices,
                partitions=partition_lags,
                total_lag=total_lag,
            )
        finally:
            await consumer.stop()
