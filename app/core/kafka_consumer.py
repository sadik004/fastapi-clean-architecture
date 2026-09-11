"""Apache Kafka Consumer Configuration & Lifecycle via aiokafka.

Provides production-hardened consumer configuration (enable_auto_commit=False,
auto_offset_reset='earliest', max_poll_records=50), consumer group isolation,
safe manual offset commit strategies, and a robust MockAIOKafkaConsumer for testing.
"""

from __future__ import annotations

import asyncio
import logging
import socket
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from app.core.config import get_settings
from app.core.kafka import get_shared_mock_kafka_storage

logger = logging.getLogger(__name__)

try:
    from aiokafka import ConsumerRecord, TopicPartition
except ImportError:  # pragma: no cover

    @dataclass(frozen=True, slots=True)
    class TopicPartition:  # type: ignore[no-redef]
        """Topic partition identifier fallback."""

        topic: str
        partition: int

    @dataclass(slots=True)
    class ConsumerRecord:  # type: ignore[no-redef]
        """Consumer record fallback."""

        topic: str
        partition: int
        offset: int
        key: bytes | None
        value: bytes
        timestamp: int


def _build_consumer_record(
    topic: str,
    partition: int,
    offset: int,
    key: bytes | None,
    value: bytes,
    timestamp: int,
) -> ConsumerRecord:
    """Safely build a ConsumerRecord instance compatible with aiokafka signature."""
    try:
        from aiokafka import ConsumerRecord as AIOKafkaRecord

        return AIOKafkaRecord(
            topic=topic,
            partition=partition,
            offset=offset,
            timestamp=timestamp,
            timestamp_type=0,
            key=key,
            value=value,
            checksum=None,
            serialized_key_size=len(key) if key else 0,
            serialized_value_size=len(value),
            headers=(),
        )
    except Exception:
        return ConsumerRecord(
            topic=topic,
            partition=partition,
            offset=offset,
            key=key,
            value=value,
            timestamp=timestamp,
        )


# Shared registry of committed offsets: group_id -> TopicPartition -> committed_offset
_shared_mock_committed_offsets: dict[str, dict[TopicPartition, int]] = defaultdict(dict)


def get_shared_mock_committed_offsets() -> dict[str, dict[TopicPartition, int]]:
    """Return shared registry of consumer group committed offsets."""
    return _shared_mock_committed_offsets


def clear_shared_mock_consumer_offsets() -> None:
    """Purge all committed offsets across all consumer groups."""
    _shared_mock_committed_offsets.clear()


class MockAIOKafkaConsumer:
    """In-memory Mock Kafka Consumer accurately simulating Kafka Consumer Groups and Manual Commits.

    Features:
      - Consumer Group Isolation: Multiple distinct consumer groups track independent offsets.
      - At-Least-Once Delivery: If uncommitted, records are re-read upon seek/recovery.
      - Manual Offset Commits: Commits only when explicitly invoked.
      - Partition Assignment: Partitions are deterministically assigned.
    """

    def __init__(
        self,
        *topics: str,
        bootstrap_servers: str = "localhost:9092",
        group_id: str = "order-processing-group",
        enable_auto_commit: bool = False,
        auto_offset_reset: str = "earliest",
        max_poll_records: int = 50,
        partitions_per_topic: int = 3,
        storage: dict[str, dict[int, list[tuple[int, bytes | None, bytes, int]]]] | None = None,
    ) -> None:
        self.topics = list(topics)
        self.bootstrap_servers = bootstrap_servers
        self.group_id = group_id
        self.enable_auto_commit = enable_auto_commit
        self.auto_offset_reset = auto_offset_reset
        self.max_poll_records = max_poll_records
        self.partitions_per_topic = partitions_per_topic
        self.logs = get_shared_mock_kafka_storage() if storage is None else storage

        self._assigned_partitions: set[TopicPartition] = set()
        self._current_positions: dict[TopicPartition, int] = {}
        self.is_started = False

        if self.enable_auto_commit:
            logger.warning(
                "CRITICAL WARNING: enable_auto_commit=True is active in consumer group %s. "
                "This can lead to silent data loss in production environments!",
                self.group_id,
            )

    async def start(self) -> None:
        """Start consumer and assign partitions for subscribed topics."""
        self.is_started = True
        self._assigned_partitions.clear()

        for topic in self.topics:
            for p in range(self.partitions_per_topic):
                tp = TopicPartition(topic, p)
                self._assigned_partitions.add(tp)

                # Initialize fetch position based on committed offset or auto_offset_reset
                group_commits = _shared_mock_committed_offsets[self.group_id]
                if tp in group_commits:
                    # In Kafka semantics, committed offset is the NEXT offset to fetch
                    self._current_positions[tp] = group_commits[tp]
                else:
                    if self.auto_offset_reset == "earliest":
                        self._current_positions[tp] = 0
                    else:
                        self._current_positions[tp] = len(self.logs[topic][p])

        logger.info(
            "MockAIOKafkaConsumer started: group_id=%s, assigned_partitions=%s",
            self.group_id,
            len(self._assigned_partitions),
        )

    async def stop(self) -> None:
        """Stop mock consumer."""
        self.is_started = False
        logger.info("MockAIOKafkaConsumer stopped: group_id=%s", self.group_id)

    def assignment(self) -> set[TopicPartition]:
        """Return currently assigned topic partitions."""
        return set(self._assigned_partitions)

    async def position(self, tp: TopicPartition) -> int:
        """Return the current fetch offset for a given topic partition."""
        return self._current_positions.get(tp, 0)

    async def committed(self, tp: TopicPartition) -> int | None:
        """Return last committed offset for this consumer group on the specified topic partition."""
        return _shared_mock_committed_offsets[self.group_id].get(tp)

    def highwater(self, tp: TopicPartition) -> int:
        """Return end offset (high watermark) of the topic partition log."""
        return len(self.logs[tp.topic][tp.partition])

    def seek(self, tp: TopicPartition, offset: int) -> None:
        """Seek consumer fetch position for a topic partition to a specific offset."""
        if tp not in self._assigned_partitions:
            raise ValueError(f"TopicPartition {tp} is not assigned to consumer group {self.group_id}")
        self._current_positions[tp] = max(0, offset)
        logger.debug("Seek %s to offset %d", tp, offset)

    async def seek_to_committed(self) -> None:
        """Seek all assigned partitions back to their last committed offset."""
        group_commits = _shared_mock_committed_offsets[self.group_id]
        for tp in self._assigned_partitions:
            if tp in group_commits:
                self._current_positions[tp] = group_commits[tp]
            else:
                self._current_positions[tp] = 0

    async def getmany(
        self,
        *partitions: TopicPartition,
        timeout_ms: int = 0,
        max_records: int | None = None,
    ) -> dict[TopicPartition, list[ConsumerRecord]]:
        """Poll and return records across assigned partitions up to max_records.

        Does NOT commit offsets. Updates fetch position only for the current in-flight read.
        """
        if not self.is_started:
            raise RuntimeError("MockAIOKafkaConsumer is not started. Call start() first.")

        active_partitions = list(partitions) if partitions else list(self._assigned_partitions)
        limit = min(max_records or self.max_poll_records, self.max_poll_records)

        result: dict[TopicPartition, list[ConsumerRecord]] = defaultdict(list)
        total_fetched = 0

        for tp in active_partitions:
            if total_fetched >= limit:
                break

            current_pos = self._current_positions.get(tp, 0)
            raw_partition_log = self.logs[tp.topic][tp.partition]

            while current_pos < len(raw_partition_log) and total_fetched < limit:
                offset, key, value, ts = raw_partition_log[current_pos]
                record = _build_consumer_record(
                    topic=tp.topic,
                    partition=tp.partition,
                    offset=offset,
                    key=key,
                    value=value,
                    timestamp=ts,
                )
                result[tp].append(record)
                current_pos += 1
                total_fetched += 1

            self._current_positions[tp] = current_pos

        return dict(result)

    async def commit(self, offsets: dict[TopicPartition, int] | None = None) -> None:
        """Manually commit offsets for this consumer group.

        If offsets is None, commits current fetch positions of all assigned partitions.
        """
        if not self.is_started:
            raise RuntimeError("MockAIOKafkaConsumer is not started. Call start() first.")

        group_commits = _shared_mock_committed_offsets[self.group_id]

        if offsets is not None:
            for tp, offset in offsets.items():
                group_commits[tp] = offset
                logger.debug(
                    "Consumer group %s committed %s at offset %d",
                    self.group_id,
                    tp,
                    offset,
                )
        else:
            for tp in self._assigned_partitions:
                pos = self._current_positions.get(tp, 0)
                group_commits[tp] = pos
                logger.debug(
                    "Consumer group %s auto-committed current pos for %s at offset %d",
                    self.group_id,
                    tp,
                    pos,
                )


async def create_kafka_consumer(
    *topics: str,
    group_id: str | None = None,
    auto_offset_reset: str | None = None,
    max_poll_records: int | None = None,
    force_mock: bool = False,
) -> Any:
    """Factory creating an AIOKafkaConsumer instance, falling back to MockAIOKafkaConsumer if broker is offline."""
    settings = get_settings()
    effective_group = group_id or settings.kafka_consumer_group_id
    effective_reset = auto_offset_reset or settings.kafka_auto_offset_reset
    effective_max = max_poll_records or settings.kafka_max_poll_records

    if force_mock:
        logger.info("Initializing MockAIOKafkaConsumer (forced): group=%s", effective_group)
        mock = MockAIOKafkaConsumer(
            *topics,
            bootstrap_servers=settings.kafka_bootstrap_servers,
            group_id=effective_group,
            enable_auto_commit=False,
            auto_offset_reset=effective_reset,
            max_poll_records=effective_max,
        )
        return mock

    try:
        # Pre-flight non-blocking 100ms socket probe
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

        import aiokafka

        logger.info(
            "Connecting live AIOKafkaConsumer to %s (group=%s)",
            settings.kafka_bootstrap_servers,
            effective_group,
        )
        real_consumer = aiokafka.AIOKafkaConsumer(
            *topics,
            bootstrap_servers=settings.kafka_bootstrap_servers,
            group_id=effective_group,
            enable_auto_commit=False,  # CRITICAL DATA LOSS PREVENTION
            auto_offset_reset=effective_reset,
            max_poll_records=effective_max,
        )
        return real_consumer
    except Exception as exc:
        logger.warning(
            "Live Kafka cluster unreachable (%s). Using MockAIOKafkaConsumer for group %s.",
            str(exc),
            effective_group,
        )
        mock = MockAIOKafkaConsumer(
            *topics,
            bootstrap_servers=settings.kafka_bootstrap_servers,
            group_id=effective_group,
            enable_auto_commit=False,
            auto_offset_reset=effective_reset,
            max_poll_records=effective_max,
        )
        return mock
