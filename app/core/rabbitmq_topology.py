"""RabbitMQ AMQP 0-9-1 Canonical Exchanges and Queue Topology.

Declares the 3 fundamental AMQP exchanges and their bindings:
1. Direct Exchange ('orders.direct'): Exact routing key matching (O(1)).
2. Fanout Exchange ('events.fanout'): Multi-queue broadcast ignoring routing keys.
3. Topic Exchange ('logs.topic'): Pattern-based wildcard matching with '*' and '#'.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from aio_pika import ExchangeType

logger = logging.getLogger(__name__)

# Exchange Names
DIRECT_EXCHANGE_NAME = "orders.direct"
FANOUT_EXCHANGE_NAME = "events.fanout"
TOPIC_EXCHANGE_NAME = "logs.topic"

# Queue Names
# Direct bound queues
QUEUE_ORDER_CREATED = "orders.created.queue"
QUEUE_ORDER_CANCELLED = "orders.cancelled.queue"

# Fanout bound queues (broadcast to all)
QUEUE_NOTIFICATION = "events.notification.queue"
QUEUE_ANALYTICS = "events.analytics.queue"
QUEUE_AUDIT = "events.audit.queue"

# Topic bound queues (pattern matching)
QUEUE_EU_LOGS = "logs.eu.queue"
QUEUE_CRITICAL_ALERTS = "logs.critical.queue"
QUEUE_ORDER_MONITORING = "logs.order.monitoring.queue"

# Routing keys and patterns
ROUTING_KEY_ORDER_CREATED = "order.created"
ROUTING_KEY_ORDER_CANCELLED = "order.cancelled"

TOPIC_PATTERN_EU = "europe.#"
TOPIC_PATTERN_CRITICAL = "#.critical"
TOPIC_PATTERN_ORDER = "order.*.*"


@dataclass(slots=True)
class RabbitMQTopology:
    """Container holding declared exchanges and queues for easy reference."""

    direct_exchange: Any
    fanout_exchange: Any
    topic_exchange: Any
    queues: dict[str, Any]


async def declare_rabbitmq_topology(channel: Any) -> RabbitMQTopology:
    """Declare canonical AMQP exchanges, queues, and bindings idempotently on the channel.

    Topological Structure:
    1. Direct Exchange (orders.direct):
       - orders.created.queue bound with routing_key = 'order.created'
       - orders.cancelled.queue bound with routing_key = 'order.cancelled'
    2. Fanout Exchange (events.fanout):
       - events.notification.queue bound (all events)
       - events.analytics.queue bound (all events)
       - events.audit.queue bound (all events)
    3. Topic Exchange (logs.topic):
       - logs.eu.queue bound with pattern 'europe.#'
       - logs.critical.queue bound with pattern '#.critical'
       - logs.order.monitoring.queue bound with pattern 'order.*.*'
    """
    logger.info("Declaring AMQP 0-9-1 exchanges...")

    # 1. Declare Exchanges
    direct_ex = await channel.declare_exchange(
        name=DIRECT_EXCHANGE_NAME,
        type=ExchangeType.DIRECT,
        durable=True,
    )

    fanout_ex = await channel.declare_exchange(
        name=FANOUT_EXCHANGE_NAME,
        type=ExchangeType.FANOUT,
        durable=True,
    )

    topic_ex = await channel.declare_exchange(
        name=TOPIC_EXCHANGE_NAME,
        type=ExchangeType.TOPIC,
        durable=True,
    )

    declared_queues: dict[str, Any] = {}

    # 2. Declare Direct Queues & Bindings
    q_created = await channel.declare_queue(QUEUE_ORDER_CREATED, durable=True)
    await q_created.bind(direct_ex, routing_key=ROUTING_KEY_ORDER_CREATED)
    declared_queues[QUEUE_ORDER_CREATED] = q_created

    q_cancelled = await channel.declare_queue(QUEUE_ORDER_CANCELLED, durable=True)
    await q_cancelled.bind(direct_ex, routing_key=ROUTING_KEY_ORDER_CANCELLED)
    declared_queues[QUEUE_ORDER_CANCELLED] = q_cancelled

    # 3. Declare Fanout Queues & Bindings
    for q_name in [QUEUE_NOTIFICATION, QUEUE_ANALYTICS, QUEUE_AUDIT]:
        q = await channel.declare_queue(q_name, durable=True)
        await q.bind(fanout_ex)
        declared_queues[q_name] = q

    # 4. Declare Topic Queues & Bindings
    q_eu = await channel.declare_queue(QUEUE_EU_LOGS, durable=True)
    await q_eu.bind(topic_ex, routing_key=TOPIC_PATTERN_EU)
    declared_queues[QUEUE_EU_LOGS] = q_eu

    q_crit = await channel.declare_queue(QUEUE_CRITICAL_ALERTS, durable=True)
    await q_crit.bind(topic_ex, routing_key=TOPIC_PATTERN_CRITICAL)
    declared_queues[QUEUE_CRITICAL_ALERTS] = q_crit

    q_order_mon = await channel.declare_queue(QUEUE_ORDER_MONITORING, durable=True)
    await q_order_mon.bind(topic_ex, routing_key=TOPIC_PATTERN_ORDER)
    declared_queues[QUEUE_ORDER_MONITORING] = q_order_mon

    logger.info("Successfully declared AMQP 0-9-1 topology with 3 exchanges and 8 queues")

    return RabbitMQTopology(
        direct_exchange=direct_ex,
        fanout_exchange=fanout_ex,
        topic_exchange=topic_ex,
        queues=declared_queues,
    )
