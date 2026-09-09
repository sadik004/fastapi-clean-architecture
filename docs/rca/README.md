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
| **Day 18** | [Repository Swapping & Test Isolation Regression](day-18_repository_swapping_and_test_isolation_regression.md) | Implemented dual-mode optional session in repository dependency; established synchronous test database purging and dependency override isolation. |
| **Day 19** | [N+1 Prevention, Lazy Raise & Migration Reversibility](day-19_n_plus_one_query_prevention_lazy_raise_and_alembic_downgrade.md) | Enforced defensive `lazy="raise"` on all relationships; eliminated N+1 queries via `selectinload`/`joinedload`; resolved SQLite foreign key downgrade ordering. |
| **Day 20** | [Protocol Read-Only Members & Error Envelope Assertion](day-20_unit_of_work_protocol_property_and_error_envelope_assertion.md) | Enforced `@property` on protocol members for getter-only implementations; asserted against standardized `ErrorResponse` envelope in API tests. |
| **Day 21** | [Open Addressing Perturbation & Tombstone Chains](day-21_compact_hash_table_open_addressing_and_tombstone_preservation.md) | Implemented CPython perturbation recurrence `$5i + 1 + perturb$` to eliminate primary clustering; preserved probe chains on deletion via dummy tombstones; bounded load factor to $\le 2/3$. |
| **Day 22** | [Slotted Dataclasses & PEP 412 Split Tables](day-22_slotted_dataclass_memory_optimization_and_pep412_split_table.md) | Enforced `@dataclass(slots=True)` across domain entities eliminating `__dict__` overhead; preserved slotted subclass hierarchies; calibrated dual-mode memory benchmarks for PEP 412. |
| **Day 23** | [Annotated Query Defaults & Trie Branch Pruning](day-23_trie_annotated_query_default_and_deletion_pruning.md) | Resolved FastAPI `Annotated` query parameter default conflict; implemented recursive bottom-up node pruning on trie deletion; enforced literal route precedence for `/autocomplete`. |
| **Day 24** | [Dataclass Comparator Dict Crash & Latency Jitter](day-24_heapq_dataclass_comparator_dict_crash_and_latency_jitter.md) | Excluded unorderable fields via `field(compare=False)` in slotted priority job dataclass; provided monotonic FIFO tie-breakers; calibrated test latency tolerance. |
| **Day 25** | [Boundary Burst Defect & Idle Memory Leaks](day-25_sliding_window_boundary_burst_and_idle_memory_leak.md) | Prevented 2x boundary spikes via Sliding Window Log continuous rolling horizon; implemented active zero-leak memory sweeps for ephemeral client IDs. |
| **Day 26** | [Binary Search Bounds & Route Shadowing](day-26_binary_search_range_boundary_invariants_and_route_shadowing.md) | Enforced required DTO transient fields in test registration payloads; resolved heterogeneous dictionary typing in strict mypy; guaranteed literal route precedence for `/filter/by-age`. |
| **Day 27** | [Perimeter Middleware & Trace ID Desync](day-27_custom_asgi_middleware_and_security_headers.md) | Enforced monotonic `time.perf_counter()` latency tracking; eliminated trace ID desynchronization between error bodies and response headers; injected 5 OWASP defensive headers across all status codes. |
| **Day 28** | [AsyncMock Invariants & TestClient Background Task Isolation](day-28_async_testing_mock_lifecycles_and_dependency_overrides.md) | Resolved TestClient background task error propagation via `raise_server_exceptions=False` to verify gateway resilience; enforced spec-bound `AsyncMock`; guaranteed dependency override state teardown. |
| **Day 29** | [Static Type Backpressure & AST Compliance](day-29_static_type_backpressure_and_ast_compliance_guard.md) | Eliminated read-only frozen Settings mutation via safe dynamic reflection without `# type: ignore`; enforced AST compliance and zero `print()` statements. |
| **Day 30** | [Skill File Drift & Architecture Consolidation](day-30_skill_file_drift_and_architectural_baseline_consolidation.md) | Sealed Month 1 foundation codex (`fastapi-core`) to prevent architectural drift; codified 10 commandments and 84 anti-patterns under milestone tag `v1.0.0-month1-core`. |
| **Day 31** | [Redis Subscriptability Collision & Connection Pool Draining](day-31_redis_runtime_subscriptability_and_connection_pool_lifecycle.md) | Resolved Python 3.13 FastAPI `eval_str=True` reflection collision on non-generic `Redis` class; implemented defensive multi-version socket draining across `redis-py` 4.x/5.x. |
| **Day 32** | [TestClient Event Loop Affinity & Method Assignment Narrowing](day-32_event_loop_cross_binding_in_testclient_and_cache_aside_resilience.md) | Eliminated `Queue is bound to a different event loop` via `AsyncClient` loop unification; replaced `# type: ignore` with `monkeypatch.setattr`; validated graceful cache fallback. |
| **Day 33** | [Schema Alignment, Write DB Bypass & Admin Auth Seeding](day-33_write_patterns_schema_alignment_and_admin_auth_seeding.md) | Prevented false cache miss tracking during write-through validation via direct repository lookup; resolved HTTP 401 via `admin_user` fixture seeding; aligned test assertions with DTO schema fields. |
| **Day 34** | [Cache Stampede, XFetch Stochastic Bounds & Mutex Locks](day-34_cache_stampede_probabilistic_early_expiration_and_xfetch_invariants.md) | Prevented cache stampede via XFetch stochastic early expiration; resolved Bandit S311 and $\ln(0)$ singularities; eliminated early stampede race conditions via non-blocking atomic mutex locks. |
| **Day 35** | [Bloom Filter Cache Penetration & Mock Isolation](day-35_bloom_filter_cache_penetration_and_mock_isolation.md) | Eliminated cache penetration via in-memory slotted Bloom filter with Kirsch-Mitzenmacher double hashing; isolated `AsyncMock` test doubles in dependency factory; prevented $h_2=0$ degeneracy. |

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
