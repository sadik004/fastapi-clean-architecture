# Root Cause Analysis (RCA) Directory

This directory logs all architecture traps, test failures, performance bottlenecks, and corrections made during our 90-Day curriculum.

---

## Complete RCA Incident Catalog

| Milestone | Topic | Primary Root Cause & Resolution |
| :--- | :--- | :--- |
| **Day 01** | [3-Tier Coupling & Linear Scan Trap](day-01_architecture_boundaries_and_domain_exceptions.md) | Decoupled domain exceptions from HTTP transport; implemented inverted hash indexes to avoid $\mathcal{O}(n)$ scans. |
| **Day 02** | [Credential Leakage & DTO Boundaries](day-02_pydantic_v2_field_constraints_and_dto_separation.md) | Segregated request/response DTOs; eliminated mutable default arguments via `Field(default_factory=...)`. |
| **Day 03** | [Index Desynchronization & Ghost Keys](day-03_repository_index_synchronization_and_ghost_keys.md) | Strictly purged inverted indexes on deletion and synchronized keys on entity updates to eliminate phantom collisions. |
| **Day 04** | [Regex Recompilation & List Scans](day-04_field_validators_regex_recompilation_and_list_lookup.md) | Pre-compiled regular expressions at module scope; replaced $\mathcal{O}(m)$ list scans with $\mathcal{O}(1)$ `frozenset` lookups. |
| **Day 05** | [model_dump() Allocation & Transient Leaks](day-05_model_validator_dump_allocation_and_transient_leakage.md) | Replaced `self.model_dump()` with direct attribute access in `@model_validator`; isolated transient validation fields. |
| **Day 06** | [Path/Query Validation & OpenAPI Traps](day-06_path_query_validation_and_openapi.md) | Polymorphic `UserNotFoundException`; OpenAPI 3.1 `anyOf` schema awareness; DTO update responsibility boundaries. |
| **Day 07** | [Pytest Lifecycle & State Isolation](day-07_pytest_fixture_lifecycle_and_test_isolation.md) | Centralized generator fixture in `conftest.py` with `autouse=True` teardown; eliminated fixture copy-paste duplication. |
| **Day 08** | [Route Order Precedence & Timing Attacks](day-08_route_order_precedence_and_constant_time_auth.md) | Placed literal sub-paths before dynamic path parameters; eliminated timing attacks using `secrets.compare_digest`. |
| **Day 09** | [Override Recursion & IDOR Ownership](day-09_dependency_override_recursion_and_idor_protection.md) | Eliminated self-referential override cycles in DAG; enforced `require_user_ownership` to eliminate IDOR profile tampering. |
| **Day 10** | [Unseeded Auth Fixture & Yield Teardown](day-10_unseeded_auth_fixture_and_yield_teardown.md) | Enforced coupled entity/header fixtures; wrapped `yield` in `try...except...finally` for guaranteed rollback and zero resource leaks. |
| **Day 11** | [Unawaited Coroutines & Gather Cancellation](day-11_async_unawaited_coroutine_and_concurrency_error_propagation.md) | Safely resolved async repo methods in sync callers via `asyncio.run()`; prevented orphaned background tasks on `asyncio.gather` failure by cancelling pending siblings. |
| **Day 12** | [Event Loop Starvation & CPU Offloading](day-12_event_loop_starvation_and_cpu_bound_offloading.md) | Offloaded CPU-heavy PBKDF2 hashing and checksum computations via `asyncio.to_thread`; preserved sub-5ms event loop health probes. |
| **Day 13** | [Dependency Lifetime & Bounded Memory](day-13_background_tasks_dependency_lifetime_and_bounded_memory.md) | Enforced strict primitive invariant for `BackgroundTasks` arguments; bounded in-memory queues using `deque(maxlen=1000)` to eliminate OOM leaks. |
| **Day 14** | [Domain Hierarchy & Safe 500 Masking](day-14_global_exception_handling_and_safe_500_masking.md) | Decoupled domain services from `HTTPException` via `BaseDomainException` hierarchy; masked 500 errors with UUID `trace_id` preventing information leakage. |
| **Day 15** | [Async Fixture Strict Mode & MissingGreenlet](day-15_pytest_asyncio_strict_fixture_and_missing_greenlet_invariant.md) | Enforced `@pytest_asyncio.fixture` in strict mode; enforced `expire_on_commit=False` on `async_sessionmaker` to prevent `MissingGreenlet` crashes; disposed pool sockets via `lifespan`. |
| **Day 17** | [Alembic Schema Drift & Metadata Namespace Pollution](day-17_alembic_schema_drift_and_metadata_namespace_pollution.md) | Isolated test entities to dedicated `TestBase` declarative bases to prevent test pollution in `Base.metadata`; simplified `downgrade()` to eliminate SQLite batch table drop errors. |

---

## RCA Entry Format

Each file is named `day-XX_<topic>.md` and follows this structure:

```markdown
# RCA: [Title of the Error / Bug]

- **Trigger**: [Test failure / Lead Architect feedback / Runtime error]
- **Faulty Code / Pattern**:
  ```python
  # Code that failed
  ```
- **Root Cause**:
  Detailed explanation of why this was flawed or sub-optimal.
- **Resolution**:
  ```python
  # Corrected implementation
  ```
- **Permanent Prevention Rule**:
  Rule added to .agents/skills/fastapi-production/SKILL.md to ensure zero recurrence.
```
