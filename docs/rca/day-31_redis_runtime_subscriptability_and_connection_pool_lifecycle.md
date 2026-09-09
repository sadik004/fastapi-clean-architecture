# RCA: Day 31 - Redis Runtime Subscriptability Collision in FastAPI Reflection and Connection Pool Socket Lifecycle

- **Date**: 2026-09-09
- **Role**: Junior Apprentice Backend Engineer
- **Lead Architect & Mentor**: User
- **Topic**: Python 3.13 Type Annotation Reflection (`eval_str=True`), `redis-py` 4.x Generic Subscriptability Collisions, and Lifespan Socket Draining

---

## 1. Trigger & Incident Scenario

During the Day 31 test suite initialization:
1. **Runtime Type Reflection Crash in FastAPI Route Registration**:
   - When registering the Redis health probe route:
     ```python
     @app.get("/health/redis", tags=["Health"])
     async def redis_health_check(redis: Redis[Any] = Depends(get_redis)) -> dict[str, Any]:
         ...
     ```
   - Running `pytest tests/test_redis_basics.py` crashed during route compilation with:
     ```text
     C:\Users\User\anaconda3\Lib\inspect.py:292: in get_annotations
         value if not isinstance(value, str) else eval(value, globals, locals)
     C:\Users\User\anaconda3\Lib\typing.py:317: in _check_generic_specialization
         raise TypeError(f"{cls} is not a generic class")
     E   TypeError: <class 'redis.asyncio.client.Redis'> is not a generic class
     ```
2. **The `types-redis` vs Runtime Divergence Trap**:
   - `types-redis` type stubs declare `class Redis(Generic[_StrType])`, leading static type checkers (`mypy --strict`) to flag unparameterized `Redis` with `[type-arg]` errors.
   - However, at runtime in `redis-py 4.6.0`, `<class 'redis.asyncio.client.Redis'>` does not implement `__parameters__` for generic specialization. When FastAPI's dependency introspection calls `inspect.signature(call, eval_str=True)` in Python 3.13, evaluating the string `"Redis[Any]"` raises `TypeError: is not a generic class`.
3. **Socket Descriptor Leak Risk on Async Client Disconnection**:
   - `redis-py` 4.x does not implement `.aclose()` (which was introduced in 5.x), instead relying on `.close()` and `await pool.disconnect()`. Calling `.aclose()` blindly in lifespan hooks causes unhandled `AttributeError` during application shutdown.

---

## 2. Faulty Code / Anti-Patterns

### Anti-Pattern A: Parameterizing Non-Generic Runtime Classes in Route Signatures
```python
# FAULTY: Python 3.13 eval_str=True crashes on Redis[Any] at runtime
@app.get("/health/redis")
async def redis_health_check(redis: Redis[Any] = Depends(get_redis)) -> dict[str, Any]:
    ...
```

### Anti-Pattern B: Subscripting `ConnectionPool` Without Runtime Class Support
```python
# FAULTY: Raises TypeError: type 'ConnectionPool' is not subscriptable on module import
_redis_pool: ConnectionPool[Any] | None = None
```

### Anti-Pattern C: Assuming Single Version Socket Closing Syntax (`.aclose()`)
```python
# FAULTY: Crashes with AttributeError in redis-py 4.x
async def close_redis_pool() -> None:
    await _redis_client.aclose()  # AttributeError: 'Redis' object has no attribute 'aclose'
```

---

## 3. Root Cause Analysis

1. **FastAPI Reflection Mechanics (`inspect.signature(eval_str=True)`)**:
   - In Python 3.13, FastAPI uses `inspect.signature(call, eval_str=True)` to introspect type hints on dependency parameters. Even with `from __future__ import annotations`, `eval_str=True` forces Python to evaluate stringified type annotations at runtime via `eval()`. When an external library class is annotated with generic brackets (`Redis[Any]`) but does not inherit from `typing.Generic` in the installed CPython distribution, `typing._check_generic_specialization` fails immediately.
2. **Version Divergence in `redis-py` Driver Lifecycles**:
   - Between `redis-py` 4.x and 5.x, async connection management underwent breaking changes. 4.x uses `client.close()` and `connection_pool.disconnect()`, while 5.x standardized on `await client.aclose()`. Robust production gateways must programmatically probe for method existence to prevent socket descriptor leaks across varying deployment environments.
3. **Mypy Override Alignment**:
   - Stubs that conflict with runtime class behaviors should be managed via targeted `[[tool.mypy.overrides]]` in `pyproject.toml` (`ignore_missing_imports = true` for `redis.*` and `fakeredis.*`), allowing the native, un-subscripted class `Redis` to be used cleanly across both runtime and compile-time boundaries.

---

## 4. Resolution & Corrective Implementation

### 1. Un-Subscripted Clean Type Signatures
Reverted type annotations to standard unparameterized `Redis` and `ConnectionPool`:

```python
# CORRECT: Clean runtime evaluation and strict static typing
_redis_pool: ConnectionPool | None = None
_redis_client: Redis | None = None

async def get_redis() -> AsyncGenerator[Redis]:
    ...

@app.get("/health/redis", tags=["Health"])
async def redis_health_check(redis: Redis = Depends(get_redis)) -> dict[str, Any]:
    ...
```

### 2. Defending Mypy Configuration via Module Overrides (`pyproject.toml`)
```toml
[[tool.mypy.overrides]]
module = ["redis", "redis.*", "fakeredis", "fakeredis.*"]
ignore_missing_imports = true
```

### 3. Defensive Multi-Version Pool & Socket Draining
Implemented version-agnostic connection cleanup in `app/core/redis.py`:

```python
async def close_redis_pool() -> None:
    global _redis_pool, _redis_client, _is_fallback

    if _redis_client is not None:
        try:
            if hasattr(_redis_client, "aclose"):
                await _redis_client.aclose()
            elif hasattr(_redis_client, "close"):
                res = _redis_client.close()
                if asyncio.iscoroutine(res):
                    await res
        finally:
            _redis_client = None

    if _redis_pool is not None:
        try:
            await _redis_pool.disconnect()
        finally:
            _redis_pool = None
```

---

## 5. Permanent Prevention & Skills Codex Verification

- **Codified Rule in `.agents/skills/fastapi-production/SKILL.md`**:
  1. **Runtime Subscriptability Verification**: Never use generic type subscripting (`Class[T]`) on third-party client drivers unless the runtime class actively subclasses `typing.Generic`. FastAPI's `inspect.signature(eval_str=True)` will evaluate the subscripting at runtime and raise fatal `TypeError` exceptions.
  2. **Defensive Async Socket Draining**: Socket cleanup routines must defensively inspect both `.aclose()` and `.close()` + `.disconnect()`, guaranteeing zero socket leaks across `redis-py` 4.x, 5.x, and in-memory mock adapters.
