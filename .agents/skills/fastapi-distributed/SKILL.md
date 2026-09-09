---
name: fastapi-distributed
description: Sealed Month 2 distributed systems foundation skill codifying Redis caching patterns (Cache-Aside, Write-Through, Write-Behind, XFetch, Bloom Filter), Concurrency Controls (Optimistic, Pessimistic with_for_update, Distributed Redlock, Idempotency Keys), Enterprise Identity & Security (Argon2id, Stateless JWT with RS256, Refresh Token Rotation, Bitmasking RBAC, ABAC, OWASP API Security, UUIDv7, Field-Level Fernet Encryption), and Asynchronous Event-Driven Messaging (Celery, Celery Beat, ARQ, RabbitMQ AMQP exchanges, Kafka Event Streaming, Dead Letter Queues, Domain Events, and Transactional Outbox Pattern).
---

# FastAPI Distributed Systems: Sealed Architectural Codex (Month 2)

**Status**: IMMUTABLE & SEALED (Days 31–60 Baseline)  
**Lead Architect & Mentor**: User  
**Apprentice Backend Engineer**: Antigravity  
**Version**: `v2.0.0-month2-distributed`  

This document serves as the permanent, authoritative architectural codex established during Month 2 of the 90-Day Enterprise FastAPI curriculum. It encapsulates distributed systems patterns, multi-node concurrency primitives, cryptographic zero-trust identity, and asynchronous event-driven messaging pipelines. All future distributed microservices, containerized workloads (Docker/Kubernetes), and resilience engines unconditionally inherit and enforce these sealed invariants.

---

## 1. Master Architectural Commandments of Month 2

1. **Zero Data Loss Invariant**:
   - Every state mutation requiring event propagation to external message brokers (Kafka, RabbitMQ) must be committed atomically with the domain entity via the **Transactional Outbox Pattern** (`UnitOfWorkProtocol`).
   - Direct dual-writing (`db.commit()` followed by `broker.publish()`) is strictly prohibited.

2. **At-Least-Once Delivery & Idempotent Consumption**:
   - Message brokers and consumers must never use automated offset commits or silent message discarding (`enable_auto_commit=False`, `auto_ack=False`).
   - Consumers must commit offsets strictly **after** database transactions succeed. Downstream receivers must enforce idempotency keys to tolerate duplicate message replay.

3. **Multi-Node Concurrency Protection**:
   - Shared mutable resources must be guarded by explicit concurrency boundaries: Optimistic Concurrency Control (OCC) with database-side row versioning for low-contention entities; Pessimistic row locking (`with_for_update`) for critical financial reservations; and Distributed Redlock mutexes with token-validated atomic Lua release for multi-pod synchronization.

4. **Zero-Trust Cryptographic Defense-in-Depth**:
   - Passwords must be hashed with Argon2id (OWASP gold standard) and offloaded to worker threads (`asyncio.to_thread`) to prevent event loop starvation.
   - PII fields must be encrypted at the ORM boundary via Fernet AES-128-CBC + HMAC-SHA256 (`TypeDecorator`). A raw SQL dump must leak zero plaintext.
   - Access tokens must be short-lived (15 minutes) and stateless; refresh tokens must be strictly single-use and revoke token families upon replay detection (RTR).

---

## 2. Pillar I: High-Throughput Distributed Caching & Probabilistic Data Structures

### 1. Redis Async Lifecycle & Connection Management
- Use `redis.asyncio` exclusively. Synchronous Redis socket operations on the async event loop are banned.
- Initialize connection pools inside the application `lifespan` hook and drain all sockets cleanly during shutdown.
- Set strict, mandatory TTL (`expire_seconds` / `ex=...`) on every ephemeral key to eliminate Redis Out-Of-Memory (OOM) crashes.

### 2. Cache-Aside & Graceful Degradation
- Read path: Query Redis $\rightarrow$ on hit, deserialize and return ($\mathcal{O}(1)$); on miss, query database, populate cache with TTL, and return.
- Mutation path: Mutate database record and unconditionally evict the corresponding cache key (`DELETE`). Never perform non-atomic cache write-throughs without eviction.
- Resiliency: Wrap cache calls in defensive `try...except` blocks. If Redis times out or disconnects, the service must gracefully fall back to the relational database without failing customer requests.

### 3. Write-Behind (Write-Back) Buffering & Atomic Multi-Exec
- High-frequency telemetry (e.g. view counters, metrics) buffers in Redis memory via `HINCRBY`.
- Flush buffers asynchronously via Celery or ARQ background workers using an atomic transactional pipeline (`MULTI ... EXEC`) to read and reset counters simultaneously, eliminating lost updates.

### 4. Cache Stampede Prevention (XFetch Probabilistic Early Expiration)
- High-throughput read targets prevent the "Thundering Herd" problem using the **XFetch algorithm**:
  $$\Delta - \beta \times \delta \times \ln(\text{random}()) > \text{TTL}$$
- When the condition is satisfied before hard TTL expiration, a non-blocking mutex lock is acquired (`SET lock:{key}:recompute 1 NX EX 10`). A single worker recomputes the query while concurrent requests continue serving the existing cached value.

### 5. Bloom Filter Cache Penetration Shield
- Protect relational databases against malicious non-existent ID queries using an in-memory or Redis-backed Bloom Filter with Kirsch-Mitzenmacher double hashing:
  $$g_i(x) = (h_1(x) + i \cdot h_2(x)) \pmod m$$
- An answer of `False` guarantees $\mathcal{O}(1)$ entity absence with zero false negatives, immediately returning 404 without querying PostgreSQL.

### 6. Real-Time Leaderboards (Redis Sorted Sets - ZSET)
- High-concurrency leaderboard operations use Redis ZSET (`ZADD`, `ZINCRBY`, `ZREVRANGE`, `ZREVRANK`, `ZSCORE`).
- Ban SQL `ORDER BY score DESC LIMIT N` for live rankings. Present ranks using 1-indexed numbering (`rank + 1`).

### 7. Rate Limiting: Distributed Sliding Window Log & Token Bucket
- **Sliding Window Log**: Uses Redis ZSET per client key. Timestamps are pushed as score and member (`ZADD`). Old entries outside the rolling window are pruned atomically with `ZREMRANGEBYSCORE key 0 (now - window)`. Remaining cardinality (`ZCARD`) is compared against the threshold.
- **Token Bucket via Lua Scripting**: Atomic replenishment and consumption inside Redis Lua scripts executed via `EVALSHA`. Preload scripts with `SCRIPT LOAD` and handle `NOSCRIPT` recovery transparently.

---

## 3. Pillar II: Enterprise Concurrency Control & Mutual Exclusion

### 1. Optimistic Concurrency Control (OCC)
- Entity tables include an integer `version` column.
- Update queries use database-side increments:
  ```sql
  UPDATE users SET ..., version = version + 1 WHERE id = :id AND version = :expected_version
  ```
- Evaluate `result.rowcount`. If `rowcount == 0`, a concurrent write occurred; immediately abort and raise `ConcurrencyConflictException`.

### 2. Pessimistic Row Locking (`with_for_update`)
- High-contention financial or inventory deductions acquire exclusive row locks using `select(ProductModel).where(...).with_for_update()`.
- Locks must be acquired in a deterministic primary key order to prevent cross-transaction deadlocks. Never make external network or HTTP calls while holding a row lock.

### 3. Distributed Mutual Exclusion (Redlock Mutex)
- Cluster-wide critical sections acquire locks via `SET lock:{key} {token} NX PX {ttl_ms}`.
- Releasing the lock must execute an atomic Lua script validating token ownership:
  ```lua
  if redis.call("get", KEYS[1]) == ARGV[1] then
      return redis.call("del", KEYS[1])
  else
      return 0
  end
  ```
- Blind `DEL` is strictly forbidden to prevent releasing another process's lock after TTL expiration.

### 4. Enterprise Idempotency Keys
- Mutating endpoints accept an `Idempotency-Key` header.
- The request payload is hashed with SHA-256. If a completed record exists with an identical key and matching hash, the cached response is replayed immediately.
- If the payload differs, reject with HTTP 422 Unprocessable Content. If a concurrent request is currently in-flight, return HTTP 409 Conflict. Records expire after 24 hours.

---

## 4. Pillar III: Enterprise Cryptographic Identity & Zero-Trust Security

### 1. Argon2id Password Hashing
- Use Argon2id with OWASP-recommended parameters ($m=64\text{MB}, t=3, p=4$).
- CPU-intensive hashing and verification must execute in threadpools via `asyncio.to_thread(hasher.hash, ...)` to preserve sub-5ms event loop responsiveness.
- Automatically rehash passwords on login if the stored parameters are outdated.

### 2. Stateless JWT & Refresh Token Rotation (RTR)
- Access tokens expire in 15 minutes, carrying user identity, roles, and bitmask permissions.
- Refresh tokens are stored in Redis with parent/child family chains. Using a previously rotated refresh token triggers automatic **Token Family Theft Revocation**, invalidating all active sessions for that user.

### 3. High-Performance Bitmasking RBAC
- Permissions are encoded as power-of-2 integer bitmasks (`Permission.READ = 1 << 0`, `WRITE = 1 << 1`).
- Checking authorization executes in $\mathcal{O}(1)$ bitwise operations:
  ```python
  has_permission = (user_bitmask & required_bitmask) == required_bitmask
  ```
- Eliminates multi-table SQL joins and schema migrations for permission changes.

### 4. Attribute-Based Access Control (ABAC)
- Fine-grained authorization evaluates policies based on Subject, Resource, Action, and Environment attributes.
- Centralized in `PolicyEngine` following strict **Default-Deny** (Least Privilege). Resource loaders return 404 before evaluation if the entity does not exist.

### 5. OWASP Top 10 API Security Perimeter
- **SSRF Defense**: Pre-flight DNS resolution inspects all resolved IP addresses against private and cloud metadata CIDRs (`10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`, `169.254.169.254`).
- **Path Traversal Defense**: Reject paths containing null bytes (`\x00`), enforce base directory sandboxing via `Path.resolve().relative_to(base_dir)`.
- **CORS Hardening**: Ban `allow_origins=["*"]` when `allow_credentials=True`.

### 6. Time-Ordered Identifiers (UUIDv7 & ULID)
- Use UUIDv7 or ULID for database primary keys. Time-ordered prefixes preserve B-Tree index locality, eliminating page fragmentation and cache evictions inherent to random UUIDv4.

### 7. Field-Level Encryption (FLE) via Fernet
- PII columns are protected using Fernet authenticated symmetric encryption (AES-128-CBC + HMAC-SHA256) inside a SQLAlchemy `TypeDecorator`.
- The ORM layer handles encryption on bind and decryption on result. Keys are managed as `SecretStr` via environment settings.

---

## 5. Pillar IV: Asynchronous Background Processing & Event-Driven Architecture

### 1. Celery Worker Production Hardening
- Enforce: (1) `task_serializer = "json"` (ban pickle); (2) `worker_prefetch_multiplier = 1` (fair dispatch); (3) `task_acks_late = True` (worker crash safety); (4) Hard/Soft time limits (300s/240s); (5) Exponential backoff with randomized jitter.
- Pass strictly JSON-serializable primitives (IDs, strings) to `task.delay()`. Never pass ORM models or database sessions.

### 2. Celery Beat Single-Leader Scheduling
- Deploy Celery Beat strictly as `replicas: 1`. Beat evaluates `celery.schedules.crontab` schedules and pushes task references into Redis in $\mathcal{O}(1)$ time.
- Heavy logic inside Beat is banned to eliminate clock drift. Expose manual ops endpoints (`POST /schedules/trigger/{task_name}`) for on-demand execution.

### 3. ARQ Coroutine Workers
- High-concurrency I/O workloads (webhooks, notifications) use ARQ coroutines running on a single event loop with 90% less RAM than Celery processes.
- Shared network clients (`httpx.AsyncClient`) are initialized in `startup(ctx)` and closed in `shutdown(ctx)` to prevent TCP socket exhaustion.

### 4. RabbitMQ AMQP 0-9-1 Messaging
- Decouple microservices across Direct, Fanout, and Topic exchanges using `aio-pika`.
- Enforce `delivery_mode=DeliveryMode.PERSISTENT` and manual message acknowledgement (`await message.ack()`). On failure, atomic requeue (`await message.nack(requeue=True)`) prevents message loss.

### 5. Apache Kafka Event Streaming
- Production producers configure `acks="all"`, `enable_idempotence=True`, `max_batch_size=16384`, and `linger_ms=10`.
- Every stateful event must specify a deterministic partition key (`key=f"order_{order.id}"`), ensuring strict FIFO ordering per entity partition.

### 6. Kafka Consumer Concurrency & Manual Offset Commits
- Always disable auto-commit (`enable_auto_commit=False`).
- Fetch bounded batches, process domain mutations within database transactions, and call `await consumer.commit()` strictly **after** database commit succeeds, guaranteeing At-Least-Once delivery.

### 7. Dead Letter Queue (DLQ) & Poison Message Isolation
- Wrap message consumption in bounded retries (`MAX_RETRIES = 3`) with exponential backoff.
- Exhausted poison messages are encapsulated in a forensic `DLQEnvelope` (payload, error, traceback, timestamp), routed to `orders.dlq`, and the primary queue is immediately acknowledged to eliminate Head-of-Line Blocking.
- Expose operational redrive endpoints (`POST /dlq/redrive`) to re-inject quarantined messages after bug fixes.

### 8. Pure Domain Events & In-Memory Event Dispatcher
- Domain events are declared as immutable, slotted dataclasses (`@dataclass(slots=True, frozen=True, kw_only=True)`).
- `EventDispatcher` dispatches events concurrently via `asyncio.gather`. Each handler is wrapped in an isolated exception firewall (`_safe_execute`), ensuring auxiliary listener failures never abort core domain transactions.

### 9. Transactional Outbox Pattern
- Permanently eliminates the Dual-Write Problem by co-locating domain entity mutations and event records within the same local ACID transaction (via `UnitOfWorkProtocol`).
- An asynchronous `OutboxRelayService` polls `PENDING` outbox records in chronological order, publishes them to Kafka with `aggregate_id` partition keys, and marks them `PUBLISHED` with UTC timestamps strictly upon broker confirmation.

---

## 6. Comprehensive Month 2 Architectural Matrices

| Pattern / Subsystem | Primary Technology | Complexity | Concurrency / Durability Guarantee |
| :--- | :--- | :--- | :--- |
| **Cache-Aside** | Redis Async Strings | Read: $\mathcal{O}(1)$, Write: $\mathcal{O}(1)$ | DB Fallback, Stale-Eviction |
| **Write-Behind** | Redis Hashes + Pipelines | Write: $\mathcal{O}(1)$, Flush: $\mathcal{O}(B)$ | Multi-Exec Atomic Reset |
| **XFetch Stampede Guard** | Stochastic Math + Redis Lock | $\mathcal{O}(1)$ | Probabilistic Mutex Recompute |
| **Bloom Filter** | Kirsch-Mitzenmacher Hashing | $\mathcal{O}(k)$ time, $\mathcal{O}(m)$ bits | Zero False Negatives |
| **Leaderboards** | Redis ZSET (SkipList + Dict) | Add/Rank: $\mathcal{O}(\log N)$, Range: $\mathcal{O}(\log N + M)$ | Real-time 1-based ranks |
| **Rate Limiter (Sliding Window)** | Redis ZSET + Pipeline | $\mathcal{O}(\log N + M)$ | Continuous Rolling Horizon |
| **Rate Limiter (Token Bucket)** | Redis Lua Scripting | $\mathcal{O}(1)$ | Atomic EVALSHA, Zero Race |
| **Optimistic Concurrency (OCC)** | PostgreSQL `version` Column | $\mathcal{O}(1)$ | Rowcount Verification |
| **Pessimistic Locking (PCC)** | SQLAlchemy `with_for_update` | $\mathcal{O}(1)$ | Exclusive Row Mutex |
| **Distributed Lock (Redlock)** | Redis `SET NX PX` + Lua | $\mathcal{O}(1)$ | Token-Validated Atomic Release |
| **Idempotency Keys** | Redis String + SHA-256 | $\mathcal{O}(1)$ | 24h Replay, Double-Spend Guard |
| **Argon2id Hashing** | Argon2-CFFI + `asyncio.to_thread` | $m=64\text{MB}, t=3, p=4$ | ASIC/GPU Resistance, Non-Blocking Loop |
| **Stateless JWT + RTR** | RS256 / HS256 + Redis Family | $\mathcal{O}(1)$ | 15m Expiry, Replay Revocation |
| **Bitmasking RBAC** | Bitwise Bitmask Primitives | $\mathcal{O}(1)$ | Zero-Join Authorization |
| **ABAC Policy Engine** | Rule Evaluator | $\mathcal{O}(P)$ | Default-Deny Least Privilege |
| **Field-Level Encryption** | Fernet (AES-128-CBC + HMAC) | $\mathcal{O}(L)$ string length | Transparent ORM Boundary |
| **Task Queue (Celery)** | Celery + Redis Broker | $\mathcal{O}(1)$ enqueue | Acks Late, Multiplier 1 |
| **Periodic Scheduling** | Celery Beat + Crontab | $\mathcal{O}(1)$ dispatch | Single-Leader Replicas: 1 |
| **Async Tasks (ARQ)** | ARQ + Redis Streams | $\mathcal{O}(1)$ enqueue | 90% Less RAM, Pooled Clients |
| **AMQP Message Broker** | RabbitMQ + `aio-pika` | $\mathcal{O}(1)$ routing | Persistent Delivery, Manual ACK |
| **Event Streaming** | Apache Kafka + `aiokafka` | $\mathcal{O}(1)$ append | `acks="all"`, Key Partitioning |
| **Consumer Groups** | Kafka Consumer Groups | $\mathcal{O}(1)$ poll | Manual Post-DB Offset Commit |
| **Dead Letter Queue (DLQ)** | Redis / RabbitMQ / Kafka | $\mathcal{O}(1)$ quarantine | Head-of-Line Unblocking |
| **Domain Event Bus** | Slotted Frozen Dataclasses | $\mathcal{O}(H)$ handlers | Strict Exception Isolation |
| **Transactional Outbox** | PostgreSQL + Relay Service | $\mathcal{O}(1)$ ACID write | Zero Data Loss Dual-Write Defense |

---

## 7. Quality & Verification Standards

To verify compliance with this codex, the repository must unconditionally pass:
1. **Full Automated Test Suite**: All 566+ unit, integration, and compliance tests pass with 100% success rate.
2. **Static Type Safety**: `mypy --strict` passes with 0 errors across 187+ source files.
3. **Linter & Formatting**: `ruff check` passes with 0 warnings.
