# RCA: Day 56 - Kafka Consumer Concurrency, Auto-Commit Silent Data Loss, and NamedTuple Signature Alignment

## Incident Classification
* **Category**: Stream Processing / Concurrency / Architectural Invariant & Library Compatibility
* **Severity**: High (Risk of silent event drop in production stream consumers; namedtuple constructor incompatibility in mock infrastructure)
* **Date**: 2026-09-10
* **Component**: `app/core/kafka_consumer.py`, `app/services/kafka_consumer_service.py`, `app/schemas/kafka_events.py`

---

## 1. Executive Summary & Root Cause
During the design and test execution of Day 56's Kafka Consumer architecture, two critical issues were investigated, mitigated, and codified:
1. **The Auto-Commit Data Loss Hazard**:
   - In default Kafka implementations, `enable_auto_commit=True` automatically commits the highest fetched offset at periodic intervals (e.g. 5000ms), independent of domain logic completion or database transaction commits.
   - If a consumer fetches 50 messages and crashes on record 12 after the auto-commit timer fires, records 13–50 are marked as acknowledged on the broker. Upon consumer recovery/rebalance, the replacement worker starts from offset 51, causing silent and irrecoverable event loss.
2. **`aiokafka.ConsumerRecord` Constructor Compatibility**:
   - `aiokafka.ConsumerRecord` is implemented as an immutable `namedtuple` containing 11 positional parameters (`topic`, `partition`, `offset`, `timestamp`, `timestamp_type`, `key`, `value`, `checksum`, `serialized_key_size`, `serialized_value_size`, `headers`).
   - Initializing it directly with only 6 positional arguments caused a `TypeError: ConsumerRecord.__init__() missing 5 required positional arguments` during mock record synthesis.
3. **Pydantic Model Forward Reference**:
   - `KafkaPollBatchResponse` contained `events: list[dict[str, Any]]` while `from typing import Any` was omitted, causing `PydanticUserError` during model validation.

---

## 2. Impact & Failure Analysis
* **Data Integrity Risk**:
  If `enable_auto_commit=True` were permitted in financial or order-processing services, network glitches or transient database failures would result in permanently dropped transactions with zero alerts.
* **Test Suite Disruption**:
  The `TypeError` in `ConsumerRecord` instantiation prevented mock batch consumption from executing during unit and integration test runs.

---

## 3. Permanent Resolution & Countermeasures

### Countermeasure 1: Banning Auto-Commit by Architectural Default
In `app/core/kafka_consumer.py`, `enable_auto_commit` is strictly defaulted to `False`:
```python
enable_auto_commit: bool = False
```
If `enable_auto_commit=True` is explicitly passed, a critical warning is logged.
In `app/services/kafka_consumer_service.py`, offsets are collected during message processing and manually committed **only after** all records in the batch succeed:
```python
if offsets_to_commit:
    await consumer.commit(offsets_to_commit)
```
If an exception is raised during processing, the commit call is bypassed. The failing message remains uncommitted and is automatically re-read on subsequent polls, guaranteeing **At-Least-Once processing**.

### Countermeasure 2: Robust Consumer Record Builder
Implemented `_build_consumer_record` in `app/core/kafka_consumer.py` supplying all 11 fields expected by `aiokafka.ConsumerRecord`:
```python
def _build_consumer_record(
    topic: str,
    partition: int,
    offset: int,
    key: bytes | None,
    value: bytes,
    timestamp: int,
) -> ConsumerRecord:
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
        return ConsumerRecord(...)
```

### Countermeasure 3: Canonical Topic Namespacing
Standardized all consumer services, schemas, and routers to use `TOPIC_ORDERS = "orders.events"` to guarantee seamless end-to-end event flow between `KafkaProducerService` and `KafkaConsumerService`.

---

## 4. Verification
* Dedicated consumer tests in `tests/test_kafka_consumer.py`: **7 passed in 0.63s**.
* Interoperability tests with producer: **14 passed in 1.19s**.
* Messaging & task queue suite: **41 passed in 7.99s**.
* Strict type checks (`mypy --strict`): **0 errors across 171 source files**.
* Linter (`ruff check`): **All checks passed**.
