# Day 32: Cache-Aside (Lazy Loading) Pattern Implementation & Invalidation

**Date**: 2026-09-09  
**Role**: Junior Apprentice Backend Engineer  
**Lead Architect & Mentor**: User  
**Milestone**: Month 2 — Distributed Systems, Caching & Security  

---

## 1. Concepts Covered Today

- **Cache-Aside (Lazy Loading) Architecture**:
  - Implemented the distributed Cache-Aside architectural pattern within `UserService.get_user_by_id`.
  - Established key namespace schema: `cache:user:{user_id}` with a 300-second (5-minute) TTL.
  - Three-tier separation: The repository layer (`UserRepositoryProtocol`) remains strictly agnostic of caching mechanics, while `UserService` orchestrates cache checks, database queries, and cache hydration.
- **Cache Lifecycle Operations**:
  - **Read (Step 1 - Hit)**: Queries Redis via `CacheService.get_str(key)`. On hit, deserialize JSON into domain `UserEntity`, record a hit telemetry event, and return immediately without database I/O.
  - **Read (Step 2 - Miss)**: On miss, record a miss telemetry event, query repository `_repo.get_by_id(user_id)`, serialize entity to JSON, write to Redis with `set_str(key, serialized, expire_seconds=300)`, and return entity.
  - **Resilient Fallback (Degradation)**: Wrapped all Redis operations in defensive `try...except` blocks. If Redis times out, disconnects, or fails, `UserService` logs a warning and degrades gracefully to direct database queries. Zero HTTP 500 errors are propagated to clients.
- **Active Cache Invalidation (Eviction)**:
  - Ensured strong consistency and eliminated stale reads on mutation operations.
  - On `update_user` (and `update_profile`): purges `f"cache:user:{user_id}"` immediately from Redis.
  - On `delete_user`: purges `f"cache:user:{user_id}"` immediately from Redis.
  - Next read after mutation is forced to lazy-load fresh data directly from the persistent storage.
- **Entity Serialization & Deserialization**:
  - Encapsulated explicit JSON serialization and deserialization for slotted dataclass `UserEntity` handling ISO-8601 formatted `datetime` (`created_at`) and nullable fields.
- **Cache Observability & Telemetry**:
  - Atomic hit and miss counters in Redis (`"metrics:cache:hits"`, `"metrics:cache:misses"`) with local fallback counters.
  - Exposed operational endpoint `GET /metrics/cache` returning `hits`, `misses`, and calculated `hit_ratio`.

---

## 2. Key Code Artifacts

- `app/services/cache_service.py`: Added telemetry tracking (`record_hit()`, `record_miss()`, `get_metrics()`, `reset_metrics()`).
- `app/schemas/metrics.py`: Defined `CacheMetricsResponse` Pydantic model with `hits`, `misses`, and `hit_ratio`.
- `app/routers/metrics_router.py`: Exposed `GET /metrics/cache` endpoint.
- `app/services/user_service.py`: Implemented Cache-Aside in `get_user_by_id`, active invalidation in `update_user` and `delete_user`, and JSON entity serialization helpers.
- `app/core/dependencies.py`: Injected `cache_service` into `get_user_service`.
- `tests/test_cache_aside.py`: 6 automated tests covering Cache Miss, Cache Hit, Update Invalidation, Delete Invalidation, Graceful Fallback on Redis Failure, and HTTP Metrics Endpoint.
- `docs/days_bn/day-32.md`: 10-part comprehensive pedagogical guide in 100% Bengali.
- `docs/days_bn/README.md`: Bengali curriculum catalog updated with Day 32 entry.
- `docs/rca/day-32_event_loop_cross_binding_in_testclient_and_cache_aside_resilience.md`: Root cause analysis on TestClient asyncio loop collisions and type-safe monkeypatching.
- `docs/rca/README.md`: Updated RCA directory with Day 32 entry.
- `.agents/skills/fastapi-production/SKILL.md`: Codified Cache-Aside Good Patterns vs Bad Patterns.

---

## 3. Verification & Quality Gates

- **New Test Suite**: `pytest tests/test_cache_aside.py -v` passed **6/6 tests** with 100% pass rate.
- **Full Test Suite Regression**: Passed all 369 tests across the entire codebase.
- **Codebase Compliance Guard**: `pytest tests/test_codebase_compliance.py -v` passed **5/5 tests** (zero print statements, 100% explicit return annotations).
- **Static Type Safety**: `mypy --strict app tests alembic` passed cleanly with **0 errors across 79 source files**.
- **Rust Linting & Formatting**: `ruff check app tests alembic` and `ruff format --check app tests alembic` passed cleanly with **0 errors**.

---

## 4. DSA & Algorithmic Complexity

| Operation | Time Complexity | Space Complexity | Description |
| :--- | :--- | :--- | :--- |
| **Cache Hit (`get_user_by_id`)** | $\mathcal{O}(1)$ | $\mathcal{O}(1)$ | In-memory key lookup in Redis hash table + JSON deserialization |
| **Cache Miss (`get_user_by_id`)** | $\mathcal{O}(1)$ (DB index lookup) | $\mathcal{O}(1)$ | DB primary key lookup + Redis `SETEX` ($\mathcal{O}(1)$) |
| **Cache Invalidation (`delete`/`update`)** | $\mathcal{O}(1)$ | $\mathcal{O}(1)$ | Redis key removal via `DEL` command ($\mathcal{O}(1)$ for single key) |
| **Telemetry Hit Ratio Calculation** | $\mathcal{O}(1)$ | $\mathcal{O}(1)$ | Atomic counter reads and division |
