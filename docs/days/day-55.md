# Day 55: Apache Kafka Event Streaming Architecture (Topics, Partitions & Event Producer with aiokafka)

**Date:** 2026-09-10  
**Topic:** Apache Kafka Event Streaming, Partitions, Append-Only Commit Logs, CloudEvents & Idempotent Producer  
**Status:** ✅ Completed | 7/7 Dedicated Tests Passing | Combined 34/34 Passing | Full Suite 542/542 Passing  

---

## 🎯 Lesson Objective

Engineer an enterprise Event Streaming Producer architecture powered by **Apache Kafka** and modern asynchronous Python (`aiokafka`) to handle high-throughput, partitioned, append-only commit logs:
1. **Kafka Connection & Producer Lifecycle Management (`app/core/kafka.py`)**:
   - Asynchronous Kafka producer setup via `aiokafka.AIOKafkaProducer`.
   - Settings in `app/core/config.py`: `kafka_bootstrap_servers = "localhost:9092"`, `kafka_client_id = "fastapi-order-producer"`.
   - Production Producer Hardening:
     - `acks = "all"`: Strongest durability guarantee; waits for all in-sync replicas to acknowledge record commit.
     - `enable_idempotence = True`: Prevents duplicate records on network retries; guarantees exactly-once semantics per partition.
     - `compression_type = "gzip"`: Compresses batch payloads to conserve network and disk I/O.
     - `max_batch_size = 16384` (16KB micro-batching).
     - `linger_ms = 10`: Buffers records up to 10ms for ultra-high throughput.
   - Resilient standalone test mock (`MockAIOKafkaProducer`) with 100ms pre-flight socket probe ensuring 100% test pass rate in offline environments.
2. **Canonical CloudEvent Models & Key-Based Partitioning (`app/schemas/kafka_events.py`)**:
   - `OrderCreatedEvent`: Standardized order event payload containing `event_id`, `event_type`, `user_id`, `order_id`, and monetary fields.
   - `PaymentProcessedEvent`: Settlement event payload containing `payment_id`, `order_id`, and `status`.
   - **Key-Based Partitioning Invariant**: Always pass deterministic string keys (e.g. `user_42`, `order_1001`). Hashing the key guarantees all events for a stateful domain entity land on the **exact same partition**, preserving strict chronological order!
3. **Producer Service Layer (`app/services/kafka_producer_service.py`)**:
   - `publish_event(topic, key, event)`: Encapsulates validation, model dumping to JSON bytes, and `send_and_wait` execution.
   - `publish_order_created(event)`: Partitioned deterministically by `user_{user_id}`.
   - `publish_payment_processed(event)`: Partitioned deterministically by `order_{order_id}`.
4. **Transport Layer (`app/routers/kafka_router.py` mounted at `/kafka`)**:
   - `POST /kafka/publish/order-created` $\rightarrow$ Returns HTTP 202 Accepted with commit metadata (topic, partition, offset).
   - `POST /kafka/publish/payment-processed` $\rightarrow$ Returns HTTP 202 Accepted.
5. **Testing & Verification Suite (`tests/test_kafka_producer.py`)**:
   - 7 unit and integration tests verifying key-based partition stability, monotonic offset incrementation, CloudEvent validation, producer hardening flags, empty key rejection, and HTTP 202 endpoints.

---

## 🏗️ RabbitMQ vs Apache Kafka: Architectural Comparison

| Architectural Dimension | RabbitMQ (AMQP 0-9-1) | Apache Kafka (Event Streaming) |
|---|---|---|
| **Core Abstraction** | Smart Broker / Dumb Consumer with transient message queues | Dumb Broker / Smart Consumer with distributed partitioned commit logs |
| **Data Retention** | Messages deleted immediately upon consumer acknowledgement (`ACK`) | Append-only immutable log; records retained for days/weeks/months (Replayability) |
| **Message Ordering** | Per-queue FIFO order (interrupted if redelivery occurs) | Strict total ordering guaranteed **per partition** via key hashing |
| **Routing Flexibility** | Sophisticated routing (Direct, Fanout, Topic wildcards, Headers) | Partition key hashing; consumers handle logical routing & stream filtering |
| **Throughput Ceiling** | Tens of thousands of msgs/sec | Millions of events/sec via sequential disk I/O and zero-copy transfer |
| **Primary Use Cases** | Inter-service RPC, targeted task processing, microservice command dispatch | High-volume clickstream, telemetry, financial audit trails, Event Sourcing, CDC |

---

## 📦 Files Created & Modified

### Created
- `app/core/kafka.py`: Asynchronous producer lifecycle, socket probe, and `MockAIOKafkaProducer` commit log engine.
- `app/schemas/kafka_events.py`: Pydantic event contracts (`OrderCreatedEvent`, `PaymentProcessedEvent`, `KafkaPublishResponse`).
- `app/services/kafka_producer_service.py`: Service coordination for deterministic key partitioning and record publishing.
- `app/routers/kafka_router.py`: FastAPI endpoints under `/kafka`.
- `tests/test_kafka_producer.py`: 7 comprehensive verification tests.
- `docs/days/day-55.md`: English architectural documentation.
- `docs/days_bn/day-55.md`: 100% Bengali pedagogical guide following the 10-part framework.
- `docs/rca/day-55_kafka_event_streaming_partition_keys_and_idempotent_producer.md`: RCA on null partition keys and idempotence.

### Modified
- `app/core/config.py`: Added Kafka bootstrap server and producer settings.
- `pyproject.toml`: Added `aiokafka`, `aiokafka.*` to mypy overrides.
- `app/main.py`: Registered Kafka producer lifecycle in lifespan; mounted `kafka_router`.
- `ROADMAP.md`: Marked Day 55 as completed `[x]`.
- `docs/days_bn/README.md`: Appended Day 55 to Bengali catalog.
- `docs/rca/README.md`: Appended Day 55 to RCA catalog.
- `.agents/skills/fastapi-production/SKILL.md`: Added Good Patterns #155, #156 and Bad Patterns #126, #127.
