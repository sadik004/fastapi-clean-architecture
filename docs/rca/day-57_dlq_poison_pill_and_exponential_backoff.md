# RCA: Day 57 - Poison Pill Messages, Head-of-Line Blocking, and Primary Queue Acknowledgment Invariants

## Incident Classification
* **Category**: Distributed Messaging / Queue Reliability & Fault Tolerance
* **Severity**: Critical (Risk of infinite consumer crash loops freezing entire stream processing pipelines)
* **Date**: 2026-09-10
* **Component**: `app/services/dlq_service.py`, `app/schemas/dlq.py`, `app/routers/dlq_router.py`

---

## 1. Executive Summary & Root Cause
In event-driven architectures utilizing message brokers (RabbitMQ) or event streams (Apache Kafka), messages that fail to parse or violate business logic invariants are known as **Poison Pills**.

### The Infinite Crash Loop & Head-of-Line Blocking
1. **The Root Cause**:
   - When a consumer pulls a poison pill message and throws an unhandled exception, naive implementations reject the message with a requeue instruction (`nack(requeue=True)` in AMQP, or leaving the offset uncommitted in Kafka).
   - Because the message returns to the front of the queue, the consumer immediately fetches it again on the very next poll cycle, throws the exact same exception, and crashes again.
   - This induces an **Infinite Crash Loop**. Because the consumer never advances past the poison pill, all healthy messages queued behind it are permanently blocked (**Head-of-Line Blocking**).

2. **The Secondary Risk (Silent Data Loss)**:
   - Conversely, naively catching the exception and dropping the message with an immediate `ack()` results in silent data loss. Ops engineers have zero record of why the message failed, what its payload was, or how to recover it.

---

## 2. Architectural Resolution & Permanent Countermeasures

### Countermeasure 1: Bounded Exponential Backoff Retry Policy
Implemented `DLQService.process_with_dlq`:
- Maximum attempts capped at `MAX_RETRIES = 3`.
- Exponential backoff schedule:
  $$\text{Delay} = \text{BASE\_DELAY} \times 2^{\text{attempt}}$$
- Transient errors (e.g. database locks, momentary network disconnects) recover within the 3 retries without burdening downstream systems or prematurely dead-lettering records.

### Countermeasure 2: Forensic DLQ Enveloping
When all 3 attempts are exhausted:
- The message is encapsulated inside a `DLQEnvelope` with:
  - `message_id`: Distributed tracking ID
  - `original_topic_or_queue`: Source origin
  - `payload`: Exact, unmutated JSON payload
  - `error_message`: Primary failure cause
  - `error_traceback`: Complete Python exception stack trace
  - `retry_count`: Total failed attempts (3)
  - `failed_at`: UTC timestamp
- The envelope is dispatched to `orders.dlq` quarantine.

### Countermeasure 3: The Primary Queue Unblocking Invariant
**Mandatory Invariant**:
Upon successfully routing the poison pill to the DLQ, the consumer **must invoke `ack_func()`** (or commit the Kafka offset) on the original primary message. Because the message is durably preserved in the DLQ, removing it from the primary stream destroys Head-of-Line Blocking and permits downstream messages to proceed immediately.

### Countermeasure 4: Operational Redrive Pipeline (`POST /dlq/redrive`)
Provided a first-class operational API enabling SREs and backend engineers to inspect quarantined messages (`GET /dlq/messages`), deploy bug fixes, and re-inject them back into the primary queue (`POST /dlq/redrive`) with zero data loss.

---

## 3. Verification Matrix
* Dedicated DLQ test suite (`tests/test_dlq_resilience.py`): **5 passed in 0.59s**.
* Combined messaging & queues test suite: **46 passed in 8.46s**.
* Linter (`ruff check`): **All checks passed**.
* Strict typing (`mypy --strict`): **0 errors across 175 source files**.
