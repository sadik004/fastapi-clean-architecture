# RCA: Day 32 - Asyncio Event Loop Cross-Binding in TestClient vs AsyncClient & Mypy Method Assignment Narrowing

- **Date**: 2026-09-09
- **Role**: Junior Apprentice Backend Engineer
- **Lead Architect & Mentor**: User
- **Topic**: `asyncio.run()` Event Loop Affinity Collisions with FakeRedis inside `TestClient`, Cache-Aside Graceful Fallback Validation, and Type-Safe Spy Monkeypatching without `# type: ignore`.

---

## 1. Trigger & Incident Scenario

During the automated verification of Day 32's Cache-Aside pattern:

### Incident 1: Event Loop Cross-Binding Defect in `TestClient`
In `tests/test_cache_aside.py`, the HTTP metrics verification test `test_cache_metrics_endpoint` was initially authored as a synchronous test using Starlette's `TestClient`. To ensure clean telemetry, the test invoked:
```python
# FAULTY: Invoking asyncio.run() inside a test with a pre-existing async fixture
cache_service = CacheService(redis_client=fake_redis)
asyncio.run(cache_service.reset_metrics())
```
Running `pytest tests/test_cache_aside.py -v` failed with:
```text
FAILED tests/test_cache_aside.py::test_cache_metrics_endpoint - assert 0 == 1
------------------------------ Captured log call ------------------------------
WARNING  app.services.user_service:user_service.py:244 Redis cache read failed for key 'cache:user:1': <Queue at 0x21f8bfe4c30 maxsize=0 tasks=2> is bound to a different event loop. Falling back to DB.
WARNING  app.services.user_service:user_service.py:261 Redis cache write failed for key 'cache:user:1': <Queue at 0x21f8bfe4c30 maxsize=0 tasks=2> is bound to a different event loop.
```

### Incident 2: Mypy Strict Method-Assignment Narrowing Error
In `test_cache_aside_hit_avoids_database_query`, when spying on `repo.get_by_id`:
```python
# FAULTY: Using broad [assignment] ignore when mypy --strict requires narrower code
repo.get_by_id = repo_spy  # type: ignore[assignment]
```
Running `mypy --strict app tests alembic` flagged:
```text
tests\test_cache_aside.py:87: error: Unused "type: ignore" comment, use narrower [method-assign] instead of [assignment] code  [unused-ignore]
Found 1 error in 1 file (checked 79 source files)
```

---

## 2. Root Cause Analysis

### A. Root Cause of the Event Loop Cross-Binding Defect
1. **Asyncio Primitives Bound to Loop of Creation**:
   - In Python 3.10+, synchronization primitives such as `asyncio.Queue` or connection pool structures capture `asyncio.get_running_loop()` at the moment of their first await or initialization.
   - The `fake_redis` fixture in `conftest.py` instantiates `fakeredis.aioredis.FakeRedis()`.
   - When `asyncio.run(cache_service.reset_metrics())` was executed, `asyncio.run()` created an ad-hoc, ephemeral event loop (Loop A), bound FakeRedis's internal queue to Loop A, executed `reset_metrics()`, and closed Loop A.
2. **Starlette `TestClient` Loop Divergence (Loop B)**:
   - Starlette's `TestClient` manages its own AnyIO event loop portal (Loop B) to run async ASGI endpoints synchronously.
   - When the HTTP request `GET /users/{user_id}` hit `UserService.get_user_by_id` inside Loop B, `FakeRedis` attempted to operate its queue on Loop B while the queue was locked to Loop A.
   - Python raised `RuntimeError: <Queue ...> is bound to a different event loop`.
3. **Graceful Degradation Proved Itself (Silver Lining)**:
   - Because `UserService` strictly adheres to our Graceful Degradation rule, the error did **not** trigger an HTTP 500 crash.
   - Instead, `UserService` caught the exception, logged `Redis cache read failed... Falling back to DB`, and queried the database.
   - Consequently, the Redis counter never incremented, causing the test assertion `assert metrics_data_1["misses"] == 1` to fail because `0 == 1`.

### B. Root Cause of Mypy `[unused-ignore]` on Method Assignment
1. In Python, methods attached to class instances are bound methods. Reassigning an instance attribute that was declared as a method signature is categorized under modern mypy as `[method-assign]`, not `[assignment]`.
2. More fundamentally, using `# type: ignore` violates our **Zero-Tolerance Static Type Safety** contract. Test spies on methods should be injected cleanly via pytest's `monkeypatch.setattr`.

---

## 3. Resolution & Code Fix

### Fix 1: Unified Async Testing with `httpx.AsyncClient`
Replaced synchronous `TestClient` with native `@pytest.mark.asyncio` and `httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")`.

```python
# CORRECT: Both test operations and FastAPI route handling run in the same pytest-asyncio loop
@pytest.mark.asyncio
async def test_cache_metrics_endpoint(fake_redis: Any, admin_auth_headers: dict[str, str]) -> None:
    """Test 6: GET /metrics/cache returns accurate hits, misses, and hit ratio over HTTP."""
    from httpx import ASGITransport, AsyncClient
    from app.main import app

    cache_service = CacheService(redis_client=fake_redis)
    await cache_service.reset_metrics()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        # Request 1: Miss
        get_res_1 = await ac.get(f"/users/{user_id}", headers=admin_auth_headers)
        assert get_res_1.status_code == 200

        # Request 2: Hit
        get_res_2 = await ac.get(f"/users/{user_id}", headers=admin_auth_headers)
        assert get_res_2.status_code == 200

        metrics_res = await ac.get("/metrics/cache")
        data = metrics_res.json()
        assert data["hits"] == 1
        assert data["misses"] == 1
        assert data["hit_ratio"] == 0.5
```

### Fix 2: Type-Safe Spy Injection via `pytest.MonkeyPatch`
Replaced direct attribute assignment with `monkeypatch.setattr`:

```python
# CORRECT: Zero # type: ignore, full mypy --strict compliance, and guaranteed teardown
@pytest.mark.asyncio
async def test_cache_aside_hit_avoids_database_query(fake_redis: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    ...
    original_get_by_id = repo.get_by_id
    repo_spy = AsyncMock(side_effect=original_get_by_id)
    monkeypatch.setattr(repo, "get_by_id", repo_spy)

    user_read_2 = await user_service.get_user_by_id(user.id)
    assert repo_spy.call_count == 0
```

---

## 4. Verification & Results

- `pytest tests/test_cache_aside.py -v`: **6 / 6 PASSED** (100%).
- `mypy --strict app tests alembic`: **Success: no issues found in 79 source files** (0 errors).
- `ruff check app tests alembic`: **All checks passed!**
- `ruff format --check app tests alembic`: **79 files already formatted**.
- Full regression suite: **369 / 369 PASSED** across the codebase.

---

## 5. Prevention Directives & Architectural Rules

1. **Rule 106 (Async Test Loop Unity)**:
   - When integration testing asynchronous endpoints that interact with stateful async dependencies (`redis.asyncio`, `fake_redis`, `asyncpg`, `AsyncSession`), NEVER mix synchronous `TestClient` with `asyncio.run()`. Always use `@pytest.mark.asyncio` and `httpx.AsyncClient` with `ASGITransport(app=app)`.
2. **Rule 107 (Zero `# type: ignore` in Test Spies)**:
   - Never override instance methods using direct assignment and `# type: ignore`. Always utilize `monkeypatch.setattr(instance, "method_name", spy_mock)` to guarantee clean type checking and automated test isolation.
3. **Rule 108 (Graceful Cache Fallback Invariant)**:
   - Always encapsulate Redis reads, writes, and deletions in `try...except Exception` blocks inside service layers. Caching must remain strictly an accelerator, never a single point of failure that produces HTTP 500 crashes.
