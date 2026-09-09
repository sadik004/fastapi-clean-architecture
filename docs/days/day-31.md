# Day 31: Redis Async Basics (Connection Pooling, Strings with TTL, Hashes & Lists)

**Date**: 2026-09-09  
**Role**: Junior Apprentice Backend Engineer  
**Lead Architect & Mentor**: User  
**Milestone**: Month 2 — Distributed Systems, Caching & Security  

---

## 1. Concepts Covered Today

- **Month 2 Distributed Systems Initialization**:
  - Initiated Phase 3 / Month 2 milestone integrating distributed in-memory state caching via Redis into our 3-tier clean architecture.
- **Centralized Redis Configuration (`app/core/config.py`)**:
  - Extended immutable Pydantic `Settings` with connection parameters:
    - `redis_url: str = "redis://localhost:6379/0"`
    - `redis_pool_size: int = 20`
    - `redis_timeout: float = 2.0`
- **Asynchronous Connection Pool & Lifespan Hooks (`app/core/redis.py`)**:
  - Configured `redis.asyncio.ConnectionPool.from_url` allocating a maximum pool of 20 reusable TCP sockets.
  - Implemented two-phase FastAPI `lifespan` integration:
    - **Startup**: Verified connection liveness via non-blocking `ping()`.
    - **Shutdown**: Gracefully disconnected pool and closed client sockets defensively handling both `redis-py` 4.x (`close()`) and 5.x (`aclose()`) APIs.
  - Provided clean FastAPI dependency provider `get_redis() -> AsyncGenerator[Redis, None]`.
  - Built graceful fallback to in-memory `fakeredis` ensuring local test suites run with 100% pass rate without requiring external daemons.
- **Encapsulated Cache Service Layer (`app/services/cache_service.py`)**:
  - Decoupled router/service layers from raw Redis drivers through a domain abstraction supporting 3 core data structures:
    - **Strings with TTL**: `set_str(key, value, expire_seconds)`, `get_str(key)`, `increment(key, amount)`.
    - **Hashes (Dictionaries)**: `hset_dict(key, mapping)`, `hget_dict(key)`, `hget_field(key, field)`.
    - **Lists (Double-Ended Queues)**: `lpush_item(key, item)`, `rpop_item(key)` (FIFO queue), `lrange_items(key, start, stop)`.
- **Live Redis Health Probe Endpoint (`GET /health/redis`)**:
  - Real-time latency measurement using high-resolution monotonic clock `time.perf_counter()`.
  - Returns HTTP 200 with round-trip `ping_ms` and pool status telemetry (`get_redis_pool_status()`).

---

## 2. Key Code Artifacts

- `app/core/config.py`: Extended `Settings` with `redis_url`, `redis_pool_size`, and `redis_timeout`.
- `app/core/redis.py`: Async connection pool manager, lifespan hooks, dependency provider, and pool telemetry.
- `app/services/cache_service.py`: High-level domain service encapsulating Strings with TTL, Hashes, and Lists.
- `app/main.py`: Lifespan hooks integration and `GET /health/redis` endpoint.
- `tests/conftest.py`: Added `fake_redis` test fixture with automatic dependency override and guaranteed cleanup.
- `tests/test_redis_basics.py`: 8 comprehensive tests covering strings, TTL expiration, atomic counters, hashes, queues, health probe, and pool telemetry.
- `docs/days_bn/day-31.md`: 10-part comprehensive pedagogical guide in 100% Bengali.
- `docs/days_bn/README.md`: Bengali curriculum catalog updated with Day 31 entry.

---

## 3. Verification & Quality Gates

- **Unit & Integration Tests**: `pytest tests/test_redis_basics.py -v` passed **8/8 tests in 1.37s** (100% pass rate).
- **Codebase Compliance Guard**: `pytest tests/test_codebase_compliance.py -v` passed **5/5 tests in 0.31s** (0 prints in production, 100% explicit return annotations).
- **Static Type Safety**: `mypy --strict app tests alembic` passed cleanly with **0 errors across 77 source files**.
- **Rust Linting & Formatting**: `ruff check app tests alembic` passed cleanly with **All checks passed!**.
- **Full Test Suite Regression**: Passed all test suites across the codebase.

---

## 4. DSA & Algorithmic Complexity

| Operation / Component | Data Structure | Time Complexity | Space Complexity |
| :--- | :--- | :--- | :--- |
| **String Get / Set** | Global In-Memory Hash Table | $\mathcal{O}(1)$ | $\mathcal{O}(\text{Key} + \text{Value})$ |
| **Atomic Increment (`INCRBY`)** | 64-bit Integer Register | $\mathcal{O}(1)$ | $\mathcal{O}(1)$ |
| **Key Expiration (TTL)** | Active Radix Tree + Passive Probabilistic Sweep | $\mathcal{O}(1)$ Lookup | $\mathcal{O}(1)$ Overhead |
| **Hash Field Get / Set** | Dict / Ziplist Compact Memory Layout | $\mathcal{O}(1)$ Per-Field | $\mathcal{O}(\text{Fields})$ |
| **List Push / Pop (`LPUSH`/`RPOP`)** | Quicklist (Linked List of Ziplists) | $\mathcal{O}(1)$ Head/Tail | $\mathcal{O}(N)$ Elements |
| **Connection Checkout** | Pre-allocated Queue / Array | $\mathcal{O}(1)$ | $\mathcal{O}(\text{pool\_size})$ |
