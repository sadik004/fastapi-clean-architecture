"""Message Broker Service Layer encapsulating AMQP publishing and consuming.

Handles JSON serialization, persistent delivery mode enforcement, topology declaration,
and safe message consumption with manual acknowledgement.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from aio_pika import DeliveryMode, Message

from app.core.rabbitmq import get_rabbitmq_channel
from app.core.rabbitmq_topology import (
    DIRECT_EXCHANGE_NAME,
    FANOUT_EXCHANGE_NAME,
    TOPIC_EXCHANGE_NAME,
    RabbitMQTopology,
    declare_rabbitmq_topology,
)

logger = logging.getLogger(__name__)

# Cached active topology reference
_active_topology: RabbitMQTopology | None = None


class MessageBrokerService:
    """Enterprise Message Broker Service managing AMQP 0-9-1 publisher and consumer flows."""

    @classmethod
    async def get_or_declare_topology(cls) -> RabbitMQTopology:
        """Retrieve active topology or declare idempotently on current channel."""
        global _active_topology
        channel = await get_rabbitmq_channel()
        if _active_topology is None:
            _active_topology = await declare_rabbitmq_topology(channel)
        return _active_topology

    @classmethod
    def reset_topology_cache(cls) -> None:
        """Reset cached topology reference (used in testing or re-connection)."""
        global _active_topology
        _active_topology = None

    @classmethod
    async def publish_direct_message(
        cls,
        routing_key: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """Publish a persistent message to the Direct Exchange ('orders.direct').

        Requires exact matching between the published routing key and the bound queue routing key.
        Guarantees O(1) routing key dispatch in RabbitMQ.
        """
        topology = await cls.get_or_declare_topology()
        body = json.dumps(payload).encode("utf-8")

        message = Message(
            body=body,
            delivery_mode=DeliveryMode.PERSISTENT,
            content_type="application/json",
        )

        logger.info(
            "Publishing Direct message to exchange '%s' with routing_key '%s'",
            DIRECT_EXCHANGE_NAME,
            routing_key,
        )

        await topology.direct_exchange.publish(message, routing_key=routing_key)

        return {
            "status": "PUBLISHED",
            "exchange": DIRECT_EXCHANGE_NAME,
            "routing_key": routing_key,
            "delivery_mode": "PERSISTENT",
            "message": f"Message published to direct exchange '{DIRECT_EXCHANGE_NAME}'.",
        }

    @classmethod
    async def publish_fanout_event(
        cls,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """Broadcast an event across all queues bound to the Fanout Exchange ('events.fanout').

        Ignores routing key completely and duplicates the message to all bound queues.
        """
        topology = await cls.get_or_declare_topology()
        body = json.dumps(payload).encode("utf-8")

        message = Message(
            body=body,
            delivery_mode=DeliveryMode.PERSISTENT,
            content_type="application/json",
        )

        logger.info(
            "Broadcasting Fanout event to exchange '%s'",
            FANOUT_EXCHANGE_NAME,
        )

        # In fanout exchanges, routing_key is ignored
        await topology.fanout_exchange.publish(message, routing_key="")

        return {
            "status": "PUBLISHED",
            "exchange": FANOUT_EXCHANGE_NAME,
            "routing_key": None,
            "delivery_mode": "PERSISTENT",
            "message": f"Event broadcasted across fanout exchange '{FANOUT_EXCHANGE_NAME}'.",
        }

    @classmethod
    async def publish_topic_message(
        cls,
        routing_key: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """Publish a message to the Topic Exchange ('logs.topic') with pattern matching.

        Routes message to queues based on wildcard patterns:
        - '*' matches exactly one word.
        - '#' matches zero or more words.
        """
        topology = await cls.get_or_declare_topology()
        body = json.dumps(payload).encode("utf-8")

        message = Message(
            body=body,
            delivery_mode=DeliveryMode.PERSISTENT,
            content_type="application/json",
        )

        logger.info(
            "Publishing Topic message to exchange '%s' with routing_key '%s'",
            TOPIC_EXCHANGE_NAME,
            routing_key,
        )

        await topology.topic_exchange.publish(message, routing_key=routing_key)

        return {
            "status": "PUBLISHED",
            "exchange": TOPIC_EXCHANGE_NAME,
            "routing_key": routing_key,
            "delivery_mode": "PERSISTENT",
            "message": f"Message published to topic exchange '{TOPIC_EXCHANGE_NAME}'.",
        }

    @classmethod
    async def consume_next_message(
        cls,
        queue_name: str,
        auto_ack: bool = False,
    ) -> dict[str, Any] | None:
        """Safely fetch next available message from specified queue with manual ACK.

        If auto_ack is False, manual acknowledgement (await message.ack()) is performed
        after successful payload decoding.
        """
        topology = await cls.get_or_declare_topology()
        if queue_name not in topology.queues:
            # If not in active topology, declare queue dynamically
            channel = await get_rabbitmq_channel()
            q = await channel.declare_queue(queue_name, durable=True)
            topology.queues[queue_name] = q

        queue = topology.queues[queue_name]
        message = await queue.get(no_ack=auto_ack, fail=False)
        if message is None:
            return None

        try:
            body_bytes = message.body if hasattr(message, "body") else b""
            payload = json.loads(body_bytes.decode("utf-8"))
            routing_key = getattr(message, "routing_key", "")
            exchange = getattr(message, "exchange", "")

            # Manual ACK invariant: Only acknowledge after complete, successful processing
            if not auto_ack:
                await message.ack()

            return {
                "status": "CONSUMED",
                "queue_name": queue_name,
                "routing_key": routing_key,
                "exchange": exchange,
                "payload": payload,
                "acknowledged": True,
            }
        except Exception as exc:
            logger.error("Error processing consumed message from '%s': %s", queue_name, exc)
            if not auto_ack:
                # Requeue message on processing failure
                await message.nack(requeue=True)
            raise
