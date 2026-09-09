"""Kafka Producer Service Layer encapsulating event publishing and key-based partitioning.

Enforces deterministic key-based hashing, JSON serialization, and structured record
metadata extraction for Apache Kafka topics.
"""

from __future__ import annotations

import logging
from typing import Any

from pydantic import BaseModel

from app.core.kafka import get_kafka_producer

logger = logging.getLogger(__name__)

# Canonical Topic Constants
TOPIC_ORDERS = "orders.events"
TOPIC_PAYMENTS = "payments.events"


class KafkaProducerService:
    """Enterprise Kafka Producer service handling event dispatching and partition invariants."""

    @classmethod
    async def publish_event(
        cls,
        topic: str,
        key: str,
        event: BaseModel,
    ) -> dict[str, Any]:
        """Publish a Pydantic event model to specified Kafka topic with deterministic key-based partitioning.

        Invariant:
        - key must be a non-empty string representing entity identity (e.g. user_id, order_id).
        - Key-based partitioning guarantees that all events with the same key land in the
          exact same partition, preserving strict chronological ordering.
        """
        if not key or not key.strip():
            raise ValueError("Kafka partition key must be a non-empty string to guarantee partition ordering.")

        producer = await get_kafka_producer()

        # Serialize Pydantic model to JSON string, then bytes
        json_payload = event.model_dump_json()
        payload_bytes = json_payload.encode("utf-8")
        key_bytes = key.encode("utf-8")

        logger.info(
            "Publishing event '%s' to topic '%s' with key '%s'",
            getattr(event, "event_type", "unknown"),
            topic,
            key,
        )

        # Append record to Kafka topic partition commit log
        record_meta = await producer.send_and_wait(
            topic=topic,
            value=payload_bytes,
            key=key_bytes,
        )

        return {
            "status": "COMMITTED",
            "topic": record_meta.topic,
            "partition": record_meta.partition,
            "offset": record_meta.offset,
            "key": key,
            "timestamp": record_meta.timestamp,
            "message": (
                f"Event appended to topic '{record_meta.topic}' "
                f"partition {record_meta.partition} at offset {record_meta.offset}."
            ),
        }

    @classmethod
    async def publish_order_created(
        cls,
        event: BaseModel,
    ) -> dict[str, Any]:
        """Publish an OrderCreatedEvent partitioned deterministically by user_id."""
        user_id = getattr(event, "user_id", None)
        order_id = getattr(event, "order_id", None)
        # Use user_id as key to group all orders from the same user into the same partition
        partition_key = f"user_{user_id}" if user_id is not None else f"order_{order_id}"

        return await cls.publish_event(
            topic=TOPIC_ORDERS,
            key=partition_key,
            event=event,
        )

    @classmethod
    async def publish_payment_processed(
        cls,
        event: BaseModel,
    ) -> dict[str, Any]:
        """Publish a PaymentProcessedEvent partitioned deterministically by order_id."""
        order_id = getattr(event, "order_id", "unknown")
        partition_key = f"order_{order_id}"

        return await cls.publish_event(
            topic=TOPIC_PAYMENTS,
            key=partition_key,
            event=event,
        )
