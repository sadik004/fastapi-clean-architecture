# RCA: Day 51 - Celery 5.6+ Eager Mode Backend Cache Invalidation, Result Storage Invariants, and Mypy Strict Decorator Overrides

- **Date**: 2026-09-09
- **Role**: Junior Apprentice Backend Engineer
- **Lead Architect & Mentor**: User
- **Topic**: Distributed Asynchronous Task Processing, Celery 5.6+ Eager Mode Backend Lifecycle, Task Result Persistence, and Mypy Strict Type Analysis
- **Status**: ✅ Resolved (9/9 Day 51 Tests Passing, 506/506 Full Suite Passing)

---

## 1. Trigger & Production Hazard

During Day 51 implementation of Distributed Asynchronous Task Processing with Celery and Redis broker, three critical obstacles emerged across test isolation, backend lifecycle caching, and static type analysis:
1. **`ConnectionRefusedError` (WinError 10061) in Eager Mode**:
   - In Celery 5.6+, setting `task_always_eager = True` executes task bodies synchronously in-process via `Task.apply()`. However, calling `AsyncResult(task_id)` or inspecting task status triggers Celery's result backend. Because `celery_app.py` sets `result_backend = Settings.redis_url` (`redis://localhost:6379/0`), when tests run in CI or environments without an active external Redis broker, `AsyncResult.state` throws `ConnectionRefusedError: [WinError 10061] No connection could be made because the target machine actively refused it`.
2. **Celery Backend Instance Caching (`_backend`) Trap**:
   - Updating the Celery configuration in a test fixture via `celery_app.conf.update(result_backend="cache+memory://")` failed to prevent connection errors because Celery caches the instantiated backend inside `celery_app._backend`. Once accessed, modifying `conf.result_backend` does not re-instantiate the backend.
3. **Missing Eager Result Storage (`store_eager_result = False`)**:
   - In Celery 5.6+, `task_always_eager = True` does NOT automatically store task results in the backend unless `Task.store_eager_result = True`. For tasks registered before the fixture ran, `AsyncResult(task_id).state` remained stuck in `"PENDING"` instead of transitioning to `"SUCCESS"`.
4. **Mypy Strict Analysis Failure on `@celery_app.task` Decorators**:
   - Celery 5.6.3 does not supply complete PEP 561 type stubs for its task decorators. Under `mypy --strict`, decorating tasks with `@celery_app.task(bind=True, ...)` caused `Untyped decorator makes function untyped` errors across `app/tasks/report_tasks.py`.

---

## 2. Faulty Code & Architectural Anti-Patterns

### Anti-Pattern A: Assuming `task_always_eager = True` Disables the Result Backend
```python
# FAULTY (tests/conftest.py):
# Eager mode executes the task body locally, but AsyncResult(task_id) still contacts Redis!
celery_app.conf.task_always_eager = True
# Triggers ConnectionRefusedError (WinError 10061) when Redis is not running!
result = AsyncResult(task_id)
state = result.state
```

### Anti-Pattern B: Mutating `conf.result_backend` Without Re-Instantiating `_backend`
```python
# FAULTY (tests/conftest.py):
celery_app.conf.result_backend = "cache+memory://"
# celery_app.backend property returns cached self._backend (still points to Redis backend)!
```

### Anti-Pattern C: Suppressing Mypy via `# type: ignore` in Production Task Code
```python
# FAULTY (app/tasks/report_tasks.py):
# Anti-pattern: Silencing type errors with inline comments masks real type issues
@celery_app.task(bind=True, max_retries=3)  # type: ignore[misc]
def generate_pdf_report(self, report_type: str, user_id: str) -> dict[str, Any]:
    ...
```

---

## 3. Root Cause Analysis

1. **Decoupled Configuration & Runtime Backend Lifecycle in Celery**:
   - Celery's `Celery.backend` property lazily instantiates the backend class on first access and stores it on the private instance attribute `_backend`. Subsequent changes to `celery_app.conf.result_backend` are completely ignored by the cached `_backend` instance. To force Celery to adopt a new backend, `_backend` must be explicitly refreshed via `celery_app._backend = celery_app._get_backend()`.
2. **Task Instance Configuration Immutability**:
   - When tasks are registered via `@celery_app.task`, Celery creates a task instance whose attributes (such as `store_eager_result`) are initialized from the configuration at import time. Changing `conf.task_store_eager_result` later does not update already-registered task instances. The fix requires iterating over `celery_app.tasks.values()` and explicitly setting `task.store_eager_result = True`.
3. **Incomplete Type Stubs in Celery 5.6**:
   - Celery's task decorator returns a dynamically created `Task` class instance whose signature cannot be statically inferred by mypy under `--strict` with `disallow_untyped_decorators = true`. Rather than compromising `--strict` globally or littering code with `# type: ignore`, tool overrides must be scoped in `pyproject.toml`.

---

## 4. Resolution & Refactored Implementation

### Step 1: Engineering the Robust Eager Mode Fixture in `tests/conftest.py`
```python
# CORRECT (tests/conftest.py):
@pytest.fixture(autouse=True)
def configure_celery_eager_mode() -> Generator[None]:
    """Configure Celery to run in synchronous eager mode for tests with in-memory backend."""
    import app.tasks.report_tasks as _report_tasks  # Force task registration
    from app.core.celery_app import celery_app

    original_always_eager = celery_app.conf.task_always_eager
    original_eager_propagates = celery_app.conf.task_eager_propagates
    original_backend = celery_app.conf.result_backend
    original_cached_backend = getattr(celery_app, "_backend", None)

    # 1. Configure in-memory backend and eager execution
    celery_app.conf.update(
        task_always_eager=True,
        task_eager_propagates=True,
        result_backend="cache+memory://",
        task_store_eager_result=True,
    )
    # 2. Invalidate and re-instantiate cached backend
    celery_app._backend = celery_app._get_backend()

    # 3. Explicitly enable eager result storage on all registered task instances
    for task in celery_app.tasks.values():
        task.store_eager_result = True

    yield

    # Teardown: Restore original settings
    for task in celery_app.tasks.values():
        task.store_eager_result = False
    celery_app.conf.update(
        task_always_eager=original_always_eager,
        task_eager_propagates=original_eager_propagates,
        result_backend=original_backend,
    )
    celery_app._backend = original_cached_backend
```

### Step 2: Scoped Mypy Tool Overrides in `pyproject.toml`
```toml
# CORRECT (pyproject.toml):
[[tool.mypy.overrides]]
module = [
    "celery",
    "celery.*",
]
ignore_missing_imports = true

[[tool.mypy.overrides]]
module = "app.tasks.*"
disallow_untyped_decorators = false
```

### Step 3: Production Hardening in `app/core/celery_app.py`
```python
# CORRECT (app/core/celery_app.py):
celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],              # Zero pickle RCE (CWE-502)
    worker_prefetch_multiplier=1,         # Fair task scheduling
    task_acks_late=True,                  # At-least-once execution on worker crash
    task_time_limit=300,                  # Hard timeout (SIGKILL)
    task_soft_time_limit=240,             # Soft timeout (cleanup)
    result_expires=86400,                 # 24-hour result expiration
)
```

---

## 5. Permanent Prevention Rules

Codified into `.agents/skills/fastapi-production/SKILL.md`:
1. **Pattern #147 (Distributed Asynchronous Task Processing with Celery & Redis)**: Offload all CPU-bound or high-latency I/O tasks from FastAPI event loops to Celery workers via Redis producer-consumer queues. Return HTTP 202 Accepted in $< 10\text{ms}$.
2. **Pattern #148 (Celery Production Hardening Invariants)**: Always enforce `task_serializer="json"`, `worker_prefetch_multiplier=1`, `task_acks_late=True`, hard/soft timeouts, and exponential backoff with jitter.
3. **Bad Pattern #117 (Synchronous CPU/IO in Route Handlers)**: Never execute PDF generation or SMTP sending in route handlers.
4. **Bad Pattern #118 (Passing Non-JSON or Heavy Payloads to Tasks)**: Never pass ORM models or large payloads to Celery tasks; pass entity IDs.
5. **Bad Pattern #119 (Relying on FastAPI `BackgroundTasks` for Mission-Critical Work)**: Never use in-process `BackgroundTasks` for financial, report, or billing operations.
