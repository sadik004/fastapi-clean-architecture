# RCA: Day 13 BackgroundTasks, The Dependency Lifetime Trap & Bounded Memory Queues

- **Trigger**: Detached dependency instances and closed session hazards when scheduling background operations; unbounded heap memory bloat under sustained load.

---

## 1. Incident 1: The Dependency Lifetime Trap (Passing Request-Scoped Objects to BackgroundTasks)

### Faulty Code / Pattern
```python
# app/routers/user_router.py
@router.post("/users/")
async def create_user(
    payload: UserCreate,
    background_tasks: BackgroundTasks,
    tx: Annotated[ScopedTransactionContext, Depends(get_transaction_context)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> UserResponse:
    user = await service.register_user(payload)
    # FATAL TRAP: Passing request-scoped dependency into background task!
    background_tasks.add_task(send_welcome_notification, db, tx, user)
    return UserResponse.model_validate(user)
```

### Root Cause
In FastAPI, dependencies utilizing generator `yield` patterns (such as database sessions, unit-of-work contexts, or transaction managers) execute Phase 2 (post-yield cleanup inside `finally:` blocks) immediately after the HTTP response headers and body are transmitted to the client. If a background task holds references to these request-scoped objects, it attempts to query closed connections or commit dead transactions, causing detached instance exceptions (`DetachedInstanceError`), closed socket failures, and connection pool exhaustion.

### Resolution
Enforce the **Strict Primitive Invariant**: Background task functions must NEVER accept request-scoped dependencies, ORM entities, or database sessions. Pass ONLY immutable primitives (`int`, `str`, `datetime`) or frozen DTOs:
```python
# app/routers/user_router.py
created_user = await service.register_user(payload=payload)

# Safe Memory Boundary: Primitives only
background_tasks.add_task(
    send_welcome_notification,
    created_user.email,
    created_user.username,
)
background_tasks.add_task(
    record_audit_log,
    "create_user",
    created_user.id,
    datetime.now(timezone.utc),
)
```

---

## 2. Incident 2: Unbounded In-Memory Queues & Heap Memory Leaks (OOM Crashes)

### Faulty Code / Pattern
```python
# app/services/notification_service.py
# Uncapped in-memory list
_AUDIT_LOG_STORE: list[AuditLogEntry] = []

async def record_audit_log(action: str, user_id: int, timestamp: datetime) -> None:
    # Memory leak! Grows infinitely: O(N) heap consumption leads to fatal Out-Of-Memory!
    _AUDIT_LOG_STORE.append(AuditLogEntry(action, user_id, timestamp))
```

### Root Cause
Using standard unbounded Python `list` collections for in-memory logging, caching, or event buffers causes memory consumption to scale linearly with the total number of incoming requests ($\mathcal{O}(N)$ space complexity). Under production traffic spikes or extended service runtimes, the Python process exhausts OS heap memory, triggering kernel Out-Of-Memory (OOM) killer terminations.

### Resolution
Enforce a hard upper bound on in-memory buffers using `collections.deque(maxlen=N)`. This guarantees strict $\mathcal{O}(1)$ insertion time, automatic $\mathcal{O}(1)$ eviction of the oldest entries, and strictly bounded $\mathcal{O}(1)$ memory consumption:
```python
# app/services/notification_service.py
from collections import deque

MAX_AUDIT_ENTRIES: int = 1000

# Bounded circular buffer with automatic eviction
_AUDIT_LOG_STORE: deque[AuditLogEntry] = deque(maxlen=MAX_AUDIT_ENTRIES)

async def record_audit_log(action: str, user_id: int, timestamp: datetime) -> None:
    try:
        entry = AuditLogEntry(action=action, user_id=user_id, timestamp=timestamp)
        _AUDIT_LOG_STORE.append(entry)
    except Exception as exc:
        logger.error("Failed to record audit log: %s", exc, exc_info=True)
```

---

## 3. Incident 3: In-Process TestClient Latency Assertion Nuance

### Faulty Code / Pattern
```python
# In test suite:
start = time.perf_counter()
response = client.post("/users/", json=payload)  # Starlette TestClient
elapsed = time.perf_counter() - start
# FAILS: TestClient awaits full ASGI app (including background tasks) before returning!
assert elapsed < 0.035
```

### Root Cause
Starlette's synchronous in-process `TestClient` wraps the ASGI application directly. While real ASGI web servers (like Uvicorn) flush `http.response.start` and `http.response.body` over the TCP socket before running `await self.background()`, `TestClient` waits for the ASGI application coroutine to exit completely before returning the response object. Thus, a background task sleeping for 50ms will cause `client.post()` in `TestClient` to take 50ms+.

### Resolution
Implement a custom streaming ASGI transport (`StreamingASGITransport`) for latency benchmarking that resolves the `Response` as soon as response body chunks are transmitted, accurately simulating network socket clients while verifying background execution happens post-response.

---

## 4. Preventive Rules Codified
1. **Strict Primitive Invariant**: Never pass request-scoped objects (`Session`, `Request`, `ScopedTransactionContext`) to `BackgroundTasks`.
2. **Bounded Memory via deque(maxlen=N)**: All in-memory buffers must enforce an upper bound to prevent OOM vulnerabilities.
3. **Resilient Exception Containment**: Wrap background tasks in defensive `try...except Exception:` blocks with structured logging so failures never crash the event loop.
