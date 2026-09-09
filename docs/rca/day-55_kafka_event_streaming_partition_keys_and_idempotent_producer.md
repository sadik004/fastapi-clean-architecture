# RCA: Apache Kafka Event Streaming, Null Partition Key Anti-Pattern & Idempotent Producer Guarantees

- **Trigger**: Day 55 architectural implementation — Building enterprise event streaming producer using Apache Kafka and `aiokafka`, resolving disordered message processing risks from null partition keys and preventing duplicate records via idempotent producer semantics.
- **Faulty Code / Pattern**:
  ```python
  # 1. Anti-Pattern: Publishing events without partition keys (null key round-robin distribution)
  await producer.send_and_wait(
      topic="orders.events",
      value=order_payload_bytes,
      key=None,  # Null key randomly scatters events across partitions!
  )
  # Event 1 (OrderCreated) lands in Partition 0.
  # Event 2 (OrderCancelled) lands in Partition 1.
  # Consumer on Partition 1 consumes OrderCancelled BEFORE Consumer on Partition 0 consumes OrderCreated!

  # 2. Anti-Pattern: Producer with acks=0 or acks=1 without idempotence
  producer = AIOKafkaProducer(
      bootstrap_servers="localhost:9092",
      acks=1,  # Only waits for leader ack; if leader dies before replication, message is permanently lost!
      enable_idempotence=False,  # On network timeout retry, duplicate messages are written to log!
  )

  # 3. Anti-Pattern: Using Kafka as a transient point-to-point task queue
  # Consuming a message and immediately deleting it or expecting broker-side per-message routing like RabbitMQ.
  ```
- **Root Cause**:
  1. **Partition Scattering & Out-of-Order Execution**: Kafka guarantees total message ordering strictly **within a single partition**. If events for a stateful domain entity (such as a specific order or user account) are published with a `null` key, Kafka applies round-robin or sticky batch distribution across all partitions. Concurrent consumers reading different partitions will process related events out of chronological sequence, causing state corruption (e.g. processing a payment refund before the payment authorization event).
  2. **Data Loss & Duplicate Records on Network Jitter**: With `acks=1`, if the Kafka partition leader writes to disk and acknowledges but crashes before follower nodes sync, the new elected leader lacks the record (data loss). Without `enable_idempotence=True`, ephemeral network timeouts cause producer retries to append identical duplicate records to the commit log.
- **Resolution**:
  1. **Enforce Deterministic Key-Based Partitioning**: Enforce non-empty string keys in `publish_event` (e.g. `user_{user_id}`, `order_{order_id}`). Kafka hashes the key (`abs(hash(key)) % num_partitions`), guaranteeing that all events for a given entity always land on the exact same partition in strict chronological FIFO sequence.
  2. **Production Producer Hardening**: Configure `acks="all"` (waiting for all In-Sync Replicas to commit) and `enable_idempotence=True` (assigning a Producer ID and monotonic sequence numbers to discard duplicate retries at the broker level).
  ```python
  # Corrected implementation in app/services/kafka_producer_service.py:
  if not key or not key.strip():
      raise ValueError("Kafka partition key must be a non-empty string to guarantee partition ordering.")

  await producer.send_and_wait(
      topic=topic,
      value=payload_bytes,
      key=key.encode("utf-8"),
  )
  ```
- **Permanent Prevention Rule**:
  - Never publish events for stateful domain entities with a `null` or empty partition key; always pass a deterministic entity identifier.
  - Always enforce `acks="all"` and `enable_idempotence=True` on Kafka producers to guarantee durability and exactly-once partition publishing.
  - Use Kafka for append-only, high-throughput event streaming and replayable audit logs; reserve RabbitMQ for point-to-point task dispatch and complex exchange routing.
