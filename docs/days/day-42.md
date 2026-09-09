# Day 42: Enterprise Idempotency Key Architecture (Preventing Duplicate Payments & Double-Spending)

## 1. Overview & Architectural Objectives
In distributed systems, networks are inherently unreliable. When clients (browsers, mobile apps, or webhook forwarders) execute state-mutating requests (such as charging credit cards or creating orders), network timeouts or packet drops can occur **after** the server has already committed the operation. If a client naively retries the POST request, the user may be billed twice or an order may be processed twice (the infamous "Double-Spend" catastrophe).

Day 42 implements an enterprise-grade **Idempotency Key Engine** adhering to Stripe and PayPal financial engineering standards:
1. **Mathematical Mutation Idempotency**:
   - For an operation $f$, executing it multiple times with the same input yields the exact same state and result:
     $$f(f(x)) = f(x)$$
2. **Deterministic State Machine in Redis**:
   - Each idempotency record under key `idempotency:{key}` progresses through strict states:
     - `"IN_PROGRESS"`: An active worker has acquired the key and is currently executing the business logic.
     - `"COMPLETED"`: The operation succeeded; caches `status_code`, `response_body`, and `response_headers`.
     - `"FAILED"`: The operation encountered an unhandled crash or validation error, cleared to allow retries.
3. **SHA-256 Payload Tampering Detection**:
   - Computes canonical deterministic SHA-256 hash over HTTP Method + URL Path + JSON payload.
   - If a client reuses an active or completed `Idempotency-Key` with different parameters (e.g. changing amount from $100 to $500), the server halts execution immediately and raises **HTTP 422 Unprocessable Content** (`IDEMPOTENCY_PAYLOAD_MISMATCH`).
4. **In-Flight Concurrency Protection**:
   - If a duplicate request arrives while the first request is still `IN_PROGRESS`, the server raises **HTTP 409 Conflict** (`CONCURRENT_REQUEST_IN_FLIGHT`), preventing concurrent dual executions.
5. **Instant Cached Response Replay**:
   - If a request is received for an already `COMPLETED` key, the business logic is bypassed entirely (Zero Charge Execution) and the cached response is replayed with header `X-Cache-Lookup: HIT-IDEMPOTENT` and status `201 Created`.
6. **Bounded Redis Memory with Explicit TTL**:
   - Every idempotency record in Redis is configured with an explicit 24-hour expiration (86,400 seconds), preventing Redis memory exhaustion over time.

---

## 2. Idempotency Request Lifecycle & State Machine

```
Client Request (POST /payments/charge + Idempotency-Key)
    │
    ▼
Compute SHA-256 Hash (Method + Path + Body)
    │
    ▼
Redis Check / Acquire
    │
    ├── Key Not Exists ───► SET idempotency:{key} IN_PROGRESS (NX=True, TTL=24h)
    │                            │
    │                            ▼
    │                     Execute PaymentService.charge()
    │                            │
    │                            ▼
    │                     Record COMPLETED in Redis
    │                            │
    │                            ▼
    │                     Return HTTP 201 Created (X-Cache-Lookup: MISS)
    │
    ├── State == "IN_PROGRESS" ──► Hash Mismatch? ──► HTTP 422 (Tampering)
    │                                  │
    │                                  └─► Hash Matches ──► HTTP 409 Conflict (Concurrent)
    │
    └── State == "COMPLETED" ──► Hash Mismatch? ──► HTTP 422 (Tampering)
                                       │
                                       └─► Hash Matches ──► HTTP 201 Created (X-Cache-Lookup: HIT-IDEMPOTENT)
                                                              [Bypasses PaymentService completely]
```

---

## 3. Core Components Implemented

### 3.1 Idempotency Engine (`app/core/idempotency.py`)
- `compute_request_hash(method, path, body)`: Sorts JSON keys deterministically and produces a SHA-256 hexadecimal digest.
- `IdempotencyManager`:
  - `check_or_acquire(key, request_hash)`: Atomic check using `SET NX` or status evaluation.
  - `record_success(key, status_code, body, headers)`: Updates record to `COMPLETED` with cached payload and maintains TTL.
  - `record_failure(key)`: Deletes record on system error to permit safe retry.

### 3.2 Domain Schemas & Layering
- `app/schemas/payment.py`: `PaymentChargeRequest` and `PaymentChargeResponse`.
- `app/services/payment_service.py`: `PaymentService` with internal execution counter verifying zero duplicate external charges.
- `app/routers/payment_router.py`: Route handling header validation, cache hits, tampering rejection, and error recovery.
- `app/main.py`: Mounted `payment_router` under prefix `""` with tags `["Payments"]`.

---

## 4. Verification & Testing

### 4.1 Test Suite (`tests/test_idempotency_key.py`)
- **Initial Charge (Cache Miss)**: Returns `201 Created`, `X-Cache-Lookup: MISS`, incrementing service charge count to 1.
- **Idempotent Replay (Cache Hit)**: Replays identical charge payload; returns `201 Created`, `X-Cache-Lookup: HIT-IDEMPOTENT`, maintaining charge count at exactly 1.
- **Payload Tampering Rejection**: Changing request payload under the same key yields `422 Unprocessable Content` with error code `IDEMPOTENCY_PAYLOAD_MISMATCH`.
- **In-Flight Concurrency Guard**: Concurrent request while state is `IN_PROGRESS` yields `409 Conflict` with error code `CONCURRENT_REQUEST_IN_FLIGHT`.
- **Unit Lifecycle & Hash Determinism**: Validates JSON formatting invariance (spacing, key sorting) produces identical cryptographic hashes.

### 4.2 Quality Gates
- `pytest tests/test_idempotency_key.py`: 6 passed in 0.37s.
- `pytest tests -q`: 432 passed in 47.90s.
- `ruff check app tests alembic`: All checks passed.
- `mypy --strict app tests alembic`: Success: no issues found in 108 source files.
