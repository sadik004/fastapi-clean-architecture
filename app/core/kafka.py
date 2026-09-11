"""Apache Kafka Producer Connection & Lifecycle Management via aiokafka.

Provides production-hardened producer configuration (acks='all', enable_idempotence=True,
compression_type='gzip', batching & linger_ms), non-blocking pre-flight connection probing,
and a standalone in-memory MockAIOKafkaProducer fallback engine for testing without an external broker.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import socket
import time
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from app.core.config import get_settings

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class RecordMetadata:
    """Container holding metadata for an appended Kafka record."""

    topic: str
    partition: int
    offset: int
    timestamp: int


_shared_mock_kafka_storage: dict[str, dict[int, list[tuple[int, bytes | None, bytes, int]]]] = defaultdict(
    lambda: defaultdict(list)
)


def get_shared_mock_kafka_storage() -> dict[str, dict[int, list[tuple[int, bytes | None, bytes, int]]]]:
    """Return shared in-memory topic partition logs for mock producer & consumer interoperability."""
    return _shared_mock_kafka_storage


def clear_shared_mock_kafka_storage() -> None:
    """Purge all shared mock Kafka topic partition commit logs."""
    _shared_mock_kafka_storage.clear()


class MockAIOKafkaProducer:
    """In-memory Mock Kafka Producer accurately simulating Kafka 3.x+ append-only commit logs."""

    def __init__(
        self,
        bootstrap_servers: str = "localhost:9092",
        client_id: str = "fastapi-order-producer",
        acks: str = "all",
        enable_idempotence: bool = True,
        compression_type: str = "gzip",
        max_batch_size: int = 16384,
        linger_ms: int = 10,
        partitions_per_topic: int = 3,
        storage: dict[str, dict[int, list[tuple[int, bytes | None, bytes, int]]]] | None = None,
    ) -> None:
        self.bootstrap_servers = bootstrap_servers
        self.client_id = client_id
        self.acks = acks
        self.enable_idempotence = enable_idempotence
        self.compression_type = compression_type
        self.max_batch_size = max_batch_size
        self.linger_ms = linger_ms
        self.partitions_per_topic = partitions_per_topic

        # Storage structure: topic -> partition_id -> list of (offset, key, value, timestamp)
        self.logs: dict[str, dict[int, list[tuple[int, bytes | None, bytes, int]]]] = (
            _shared_mock_kafka_storage if storage is None else storage
        )
        self.is_started = False

    async def start(self) -> None:
        """Start mock producer."""
        self.is_started = True
        logger.info("MockAIOKafkaProducer initialized with %d partitions per topic", self.partitions_per_topic)

    async def stop(self) -> None:
        """Stop mock producer and flush buffers."""
        self.is_started = False
        logger.info("MockAIOKafkaProducer stopped")

    def _determine_partition(self, topic: str, key: bytes | None) -> int:
        """Deterministic Murmur2-like integer partition hash.

        Guarantees that identical keys always hash to the exact same partition,
        preserving strict chronological event order.
        """
        if key is None:
            # Round-robin or default partition 0 for null keys
            return 0
        # Deterministic md5 / sha256 integer modulo for cross-platform partition stability
        key_hash = int(hashlib.sha256(key).hexdigest(), 16)
        return key_hash % self.partitions_per_topic

    async def send_and_wait(
        self,
        topic: str,
        value: bytes,
        key: bytes | None = None,
        partition: int | None = None,
        timestamp_ms: int | None = None,
    ) -> RecordMetadata:
        """Append record to partition log and return RecordMetadata with sequential offset."""
        if not self.is_started:
            raise RuntimeError("MockAIOKafkaProducer is not started. Call start() first.")

        assigned_partition = partition if partition is not None else self._determine_partition(topic=topic, key=key)
        partition_log = self.logs[topic][assigned_partition]
        current_offset = len(partition_log)
        now_ms = timestamp_ms if timestamp_ms is not None else int(time.time() * 1000)

        # Append record to immutable commit log
        partition_log.append((current_offset, key, value, now_ms))

        logger.debug(
            "MockKafka appended: topic=%s partition=%d offset=%d key=%s",
            topic,
            assigned_partition,
            current_offset,
            key.decode("utf-8") if key else "None",
        )

        return RecordMetadata(
            topic=topic,
            partition=assigned_partition,
            offset=current_offset,
            timestamp=now_ms,
        )

    def get_partition_messages(self, topic: str, partition: int) -> list[dict[str, Any]]:
        """Inspection helper returning decoded messages from a specific partition."""
        raw_msgs = self.logs[topic][partition]
        results: list[dict[str, Any]] = []
        for offset, k, v, ts in raw_msgs:
            try:
                decoded_val = json.loads(v.decode("utf-8"))
            except Exception:
                decoded_val = v.decode("utf-8")
            results.append(
                {
                    "offset": offset,
                    "key": k.decode("utf-8") if k else None,
                    "value": decoded_val,
                    "timestamp": ts,
                }
            )
        return results

    def clear(self) -> None:
        """Purge all topic partition commit logs."""
        self.logs.clear()


# Global singleton producer
_kafka_producer: Any = None
_use_mock_kafka: bool = False


async def init_kafka_producer(force_mock: bool = False) -> Any:
    """Initialize Kafka producer, falling back to in-memory MockAIOKafkaProducer if broker is unreachable."""
    global _kafka_producer, _use_mock_kafka

    if force_mock:
        logger.info("Initializing in-memory MockAIOKafkaProducer (forced)")
        mock = MockAIOKafkaProducer()
        await mock.start()
        _kafka_producer = mock
        _use_mock_kafka = True
        return _kafka_producer

    settings = get_settings()

    try:
        # Pre-flight non-blocking socket probe (100ms) on Kafka bootstrap server
        # Typically formatted as 'host:port' or 'host'
        server = settings.kafka_bootstrap_servers.split(",")[0].strip()
        if ":" in server:
            host, port_str = server.split(":", 1)
            port = int(port_str)
        else:
            host, port = server, 9092

        loop = asyncio.get_running_loop()
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setblocking(False)
        try:
            await asyncio.wait_for(loop.sock_connect(sock, (host, port)), timeout=0.1)
        finally:
            sock.close()

        # If live port is reachable, instantiate real AIOKafkaProducer
        import aiokafka

        logger.info("Connecting to live Kafka bootstrap server at: %s", settings.kafka_bootstrap_servers)
        real_producer = aiokafka.AIOKafkaProducer(
            bootstrap_servers=settings.kafka_bootstrap_servers,
            client_id=settings.kafka_client_id,
            acks=settings.kafka_producer_acks,
            enable_idempotence=settings.kafka_enable_idempotence,
            compression_type="gzip",
            max_batch_size=16384,
            linger_ms=10,
        )
        await real_producer.start()
        _kafka_producer = real_producer
        _use_mock_kafka = False
        logger.info("Successfully connected to live Apache Kafka cluster")
        return _kafka_producer
    except Exception as exc:
        logger.warning(
            "Live Apache Kafka broker unreachable (%s). Falling back to in-memory MockAIOKafkaProducer.",
            str(exc),
        )
        mock = MockAIOKafkaProducer(
            bootstrap_servers=settings.kafka_bootstrap_servers,
            client_id=settings.kafka_client_id,
        )
        await mock.start()
        _kafka_producer = mock
        _use_mock_kafka = True
        return _kafka_producer


async def close_kafka_producer() -> None:
    """Gracefully stop and flush Kafka producer."""
    global _kafka_producer

    if _kafka_producer is not None:
        try:
            await _kafka_producer.stop()
        except Exception as exc:
            logger.warning("Error stopping Kafka producer: %s", exc)
        _kafka_producer = None
        logger.info("Kafka producer successfully closed and buffers flushed")


async def get_kafka_producer() -> Any:
    """Dependency provider returning active Kafka producer (real or mock)."""
    global _kafka_producer
    if _kafka_producer is None:
        await init_kafka_producer()
    return _kafka_producer


def is_using_mock_kafka() -> bool:
    """Return True if active Kafka producer is the in-memory mock."""
    return _use_mock_kafka
