# Day 13: FastAPI BackgroundTasks Architecture & Safe Memory Lifecycle

**Date**: 2026-09-08  
**Role**: Junior Apprentice Backend Engineer  
**Lead Architect & Mentor**: User

---

## 1. Concepts Covered Today
- **The BackgroundTasks Pattern**:
  - FastAPI's `BackgroundTasks` allows endpoints to schedule operations that execute **after** the HTTP response headers and body are fully transmitted to the client.
  - Offloaded slow peripheral jobs (welcome notification dispatching with simulated 50ms latency, audit log recording) from the primary request-response latency path.
  - Verified low-latency response delivery (< 35ms) on `POST /users/` while peripheral background tasks execute post-response.
- **The Dependency Lifetime Trap (Safe Memory Boundaries)**:
  - In FastAPI, request-scoped dependencies (e.g., database sessions, transaction contexts `ScopedTransactionContext`, or raw `Request` objects) are torn down inside `finally:` blocks upon sending the HTTP response.
  - Passing request-scoped dependencies into background tasks causes catastrophic failures: detached ORM instances, closed connection errors, or connection pool leaks.
  - **Strict Invariant**: Background task functions must accept **ONLY immutable primitives or frozen DTOs** (`user_id: int`, `email: str`, `username: str`, `timestamp: datetime`), completely decoupling the background task from the request lifecycle.
- **Resilient Background Worker Tasks (`app/services/notification_service.py`)**:
  - `send_welcome_notification(email: str, username: str)`: Simulates external notification dispatch with `await asyncio.sleep(0.05)`.
  - `record_audit_log(action: str, user_id: int, timestamp: datetime)`: Records event into an in-memory audit store.
  - Defensive Resilience: Wrapped worker task bodies in defensive `try...except Exception:` blocks with structured logging (`logger.error(...)`) so failures in background tasks never crash the event loop or terminate the ASGI process.
- **Bounded In-Memory Queue (Preventing OOM Leaks)**:
  - In-memory event and audit stores must never grow unbounded.
  - Implemented bounded buffers with `collections.deque(maxlen=1000)`:
    - Guarantees strict $\mathcal{O}(1)$ insertion time.
    - Guarantees strictly bounded $\mathcal{O}(1)$ space complexity.
    - Automatically evicts the oldest entries under continuous load without manual slice allocations.

---

## 2. Architectural Decisions Made
- **Strict 3-Tier Layering for Background Tasks**:
  - **Routers (`app/routers/user_router.py`)**: Accept `background_tasks: BackgroundTasks` and only call `background_tasks.add_task(service_func, *primitive_args)`. Routers contain zero task business logic or storage logic.
  - **Services (`app/services/notification_service.py`)**: Implement worker task coroutines, business rules, bounded storage collections, and exception containment.
  - **Repositories (`app/repositories/`)**: Storage abstraction for domain entities remains insulated from ephemeral background queues.
- **Safe Memory Boundary Invariant**:
  - Endpoint `POST /users/` schedules `send_welcome_notification` with `created_user.email` and `created_user.username` (primitives).
  - Endpoints `POST /users/` and `PUT /users/{user_id}` schedule `record_audit_log` with action string, user ID integer, and UTC timestamp.
  - Verified via runtime introspection (`inspect.signature`) that no request-scoped objects can leak into task parameters.

---

## 3. DSA Time & Space Complexity Enforced
- **Audit & Notification Storage Buffer**:
  - Data Structure: `collections.deque(maxlen=1000)` (C-level doubly linked circular buffer).
  - Append Time Complexity: Strictly $\mathcal{O}(1)$.
  - Eviction Time Complexity: Strictly $\mathcal{O}(1)$ automatic drop from left when buffer reaches capacity.
  - Space Complexity: Strictly bounded $\mathcal{O}(1)$ space ($N \le 1000$), eliminating heap bloat and Out-Of-Memory (OOM) vulnerabilities.
- **Response Latency vs Background Concurrency**:
  - HTTP Transport Latency: $< 35\text{ms}$ response transmission time for `POST /users/`.
  - Background Task Latency: $\approx 50\text{ms}$ simulated I/O execution runs cooperatively on the event loop post-response without blocking subsequent HTTP requests.

---

## 4. Summary of Test Results & Quality Gates
- **Pytest**: 242 passed in 11.79s (`100%` pass rate across 28 test modules).
  - `tests/test_background_tasks.py`: 7 comprehensive tests passing:
    1. `test_low_latency_response_and_post_response_execution`: Verifies `POST /users/` returns HTTP 201 immediately (< 35ms) across the ASGI response boundary while 50ms background task runs post-response.
    2. `test_background_task_execution_on_user_registration`: Verifies welcome notification dispatch and `create_user` audit logging.
    3. `test_background_task_execution_on_user_update`: Verifies `PUT /users/{user_id}` triggers `update_user` audit logging.
    4. `test_resilient_exception_handling_in_background_tasks`: Simulates dropped connection in notification worker; verifies exception containment and structured error logging.
    5. `test_audit_log_resilient_exception_handling`: Simulates storage failure; verifies defensive exception handling.
    6. `test_bounded_memory_queue_eviction`: Inserts 1,050 entries into audit deque; verifies exact 1,000 capacity bound and eviction of oldest 50 entries.
    7. `test_safe_memory_boundary_primitive_arguments_only`: Introspects task signatures to enforce primitive-only typing and absence of request-scoped dependencies.
  - All 235 previous tests continue to pass with zero regressions.
- **Mypy**: `Success: no issues found in 32 source files` (`mypy --strict app tests`).
- **Ruff**: `All checks passed!` across `app/` and `tests/`.
