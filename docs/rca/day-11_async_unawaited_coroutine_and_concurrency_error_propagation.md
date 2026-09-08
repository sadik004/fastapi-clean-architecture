# RCA: Day 11 Asynchronous Protocol Transition, Unawaited Coroutines & Concurrency Orchestration

- **Trigger**: `AttributeError: 'coroutine' object has no attribute 'is_active'` and `RuntimeWarning: coroutine 'InMemoryUserRepository.get_by_id' was never awaited` during Day 11 test execution.

---

## 1. Incident 1: Unawaited Coroutine Object in Synchronous Test Callers

### Faulty Code / Pattern
```python
# tests/test_auth_dependencies.py (and tests/test_path_query_validation.py)
def test_get_users_me_inactive_user_returns_403(
    client: TestClient,
    standard_user: dict[str, Any],
    auth_headers: dict[str, str],
) -> None:
    user_id = int(standard_user["id"])
    repo = get_user_repository()
    user = repo.get_by_id(user_id)  # Returns coroutine object! Not awaited!
    user.is_active = False          # AttributeError: 'coroutine' object has no attribute 'is_active'
```

### Root Cause
When `UserRepositoryProtocol` and `InMemoryUserRepository` were modernized from synchronous methods to asynchronous coroutines (`async def get_by_id(...)`), invoking `repo.get_by_id(user_id)` without an `await` expression does not execute the function body; instead, it returns a generator-like coroutine object. When synchronous test functions attempted to mutate attributes directly on this returned coroutine object, Python raised an `AttributeError`, and subsequent garbage collection of the unawaited coroutine triggered `RuntimeWarning: coroutine 'InMemoryUserRepository.get_by_id' was never awaited`.

### Resolution
In synchronous test environments using FastAPI's `TestClient`, invoke asynchronous repository methods within `asyncio.run(repo.get_by_id(user_id))` to safely drive the coroutine to completion on a dedicated event loop without requiring a full async test runner rewrite:
```python
# tests/test_auth_dependencies.py
import asyncio

def test_get_users_me_inactive_user_returns_403(
    client: TestClient,
    standard_user: dict[str, Any],
    auth_headers: dict[str, str],
) -> None:
    user_id = int(standard_user["id"])
    repo = get_user_repository()
    user = asyncio.run(repo.get_by_id(user_id))
    assert user is not None
    user.is_active = False
```

---

## 2. Incident 2: Preventing Orphaned Background Tasks in `asyncio.gather`

### Faulty Code / Pattern
```python
# app/services/user_service.py (Naive gather without sibling task cancellation)
async def get_user_dashboard(self, user_id: int) -> dict[str, Any]:
    # If get_by_id raises UserNotFoundException, sibling I/O coroutines
    # continue executing in the background unmanaged on the event loop!
    results = await asyncio.gather(
        self.get_user_by_id(user_id),
        self._fetch_activity_logs(user_id),
        self._fetch_account_stats(user_id),
    )
    ...
```

### Root Cause
By default, when `asyncio.gather(*coros)` encounters an unhandled exception in one coroutine, it immediately raises that exception to the caller. However, any sibling coroutines passed to `gather` that are still running will continue to run in the background until they complete, consuming CPU, network, and memory resources as orphaned tasks.

### Resolution
Wrap concurrent task execution inside an explicit task management block or use Python 3.11+ `asyncio.TaskGroup`. When using explicit tasks with `asyncio.gather`, catch exceptions and cancel all pending sibling tasks before re-raising:
```python
# app/services/user_service.py
tasks: list[asyncio.Task[Any]] = [
    asyncio.create_task(self.get_user_by_id(user_id)),
    asyncio.create_task(self._fetch_activity_logs(user_id)),
    asyncio.create_task(self._fetch_account_stats(user_id)),
]

try:
    results = await asyncio.gather(*tasks)
    return {
        "profile": results[0],
        "activity_logs": results[1],
        "stats": results[2],
    }
except Exception:
    for t in tasks:
        if not t.done():
            t.cancel()
    raise
```

---

## 3. Preventive Rules Codified
1. **Always Await Coroutines**: When modernizing protocols to `async def`, search and update all direct call sites across services, dependencies, and test fixtures to either `await` the call or resolve via `asyncio.run()`.
2. **Explicit Task Cancellation on Gather Failures**: Always cancel lingering in-flight sibling tasks when a concurrent batch operation encounters a failure to maintain zero task leakage.
3. **Keep Pure State Cleaners Synchronous**: Utility reset methods like `repo.clear()` that operate strictly on in-memory collections without I/O should remain synchronous to avoid forcing unnecessary async loop setup on test fixtures.
