---
name: fastapi-core
description: Sealed Month 1 foundation skill codifying 3-tier clean architecture, Pydantic v2 contracts, Asyncio event loops, SQLAlchemy 2.0 async engine & pooling, Alembic migrations, UoW atomic transactions, high-performance in-memory DSA (dict internals, __slots__, Prefix Trie, Priority Queue, Sliding Window, Binary Search), custom ASGI middleware, and 100% strict type backpressure.
---

# FastAPI Core Foundation: Sealed Architectural Codex (Month 1)

**Status**: IMMUTABLE & SEALED (Days 01–30 Baseline)  
**Lead Architect & Mentor**: User  
**Apprentice Backend Engineer**: Antigravity  
**Version**: `v1.0.0-month1-core`  

This document serves as the permanent, authoritative codification of the foundational architectural laws, algorithmic complexity constraints, and engineering patterns established during Month 1 of the 90-Day Enterprise FastAPI curriculum. All future single-node and distributed systems developed in Month 2 (Persistence, Security, Caching, Messaging, Observability) unconditionally inherit and respect these core laws.

---

## 1. Core Architectural Laws

1. **3-Tier Clean Architecture Separation**:
   - `app/routers/`: HTTP routing, request parsing, response status codes, OpenAPI metadata. Zero DB queries or heavy business logic.
   - `app/services/`: Pure business logic, algorithmic transformations, domain orchestration. Transport-agnostic (raises domain exceptions, never `HTTPException`).
   - `app/repositories/`: Data persistence, storage queries, SQL operations. Abstracted behind `typing.Protocol` interfaces.
2. **Single Evolving Codebase Invariant**:
   - All production application code lives, evolves, and is refactored inside `app/`.
   - All automated test suites reside in `tests/`.
   - Isolated day or topic folders (`day01/`, `day02/`) are strictly forbidden. The system grows as a unified enterprise product.
3. **Compile-Time Static Type Backpressure**:
   - Zero-tolerance static typing under `mypy --strict` with `pydantic.mypy` plugin across all modules (`app/`, `tests/`, `alembic/`).
   - Disallow untyped defs, untyped generics, and implicit re-exports.
   - Ban loose `Any` shortcuts in favor of generic type variables (`TypeVar`), `Union` / `T | None`, and explicit DTOs.
   - Ban `# type: ignore` workarounds. All type conflicts must be resolved through proper architectural modeling or dynamic reflection in test doubles.

---

## 2. Pydantic v2 & DTO Boundary Contracts

4. **DTO Segregation**: Independent schemas for creation (`UserCreate`), mutation (`UserUpdate`), and response projection (`UserResponse`). Internal entity state (`password_hash`) must never leak to API responses.
5. **C/Rust-Speed Field Validation**: Declare constraints (`min_length`, `max_length`, `ge`, `le`, `pattern`) inside `Field()` to leverage `pydantic-core`.
6. **Pre-Compiled Regex & Frozen Sets**: Module-level pre-compilation (`re.compile`) eliminates per-request compilation latency. Module-level `frozenset` enables $\mathcal{O}(1)$ membership lookups for reserved keywords.
7. **Schema-Level Sanitization (`@field_validator`)**: String trimming, lowercase normalization, and HTML sanitization execute in `mode='before'`; business invariant validation executes in `mode='after'`.
8. **Model-Level Invariants (`@model_validator(mode='after')`)**: Enforce cross-field constraints (e.g. password confirmation matches) by accessing attributes directly on `self` (`self.password`), avoiding expensive `self.model_dump()` dictionary allocations.
9. **Transient Validation Fields**: Validation-only fields (`password_confirm`) remain strictly transient to creation schemas and never leak into domain models or database entities.
10. **Entity Hydration**: Response DTOs configure `model_config = ConfigDict(from_attributes=True)` for safe serialization from slotted domain entities.

---

## 3. FastAPI Dependency Injection DAG & Lifecycles

11. **Declarative Inversion of Control**: Inject repository protocols, services, and security dependencies via FastAPI's `Depends()`.
12. **Cached Immutable Settings**: Manage configuration via an immutable Pydantic Settings model (`frozen=True`) sourced through an `@lru_cache()` provider.
13. **Sub-Dependency Chaining & IDOR Defense**: Chain authentication (`get_current_user`) and parameter validation (`Path(...)`) in authorization dependencies (`require_user_ownership`) to reject IDOR attacks before endpoint bodies execute.
14. **Parameterized Class Dependencies**: Implement complex authorization guards as callable classes (`RoleChecker([UserRole.ADMIN])`) with $\mathcal{O}(1)$ `frozenset` role lookups.
15. **Constant-Time Secret Comparison**: Always compare secrets, tokens, and keys using `secrets.compare_digest(a, b)` to defeat timing side-channel attacks.
16. **Literal Path Route Precedence**: Declare literal routes (e.g. `/users/me`) strictly before parameterized endpoints (`/users/{user_id}`) to prevent HTTP 422 routing collisions.
17. **Two-Phase Generator Dependencies**: Wrap resource acquisition and teardown in generator dependencies with `try...yield...except...finally:`. Roll back on exception and unconditionally release sessions in `finally:`.

---

## 4. Asyncio Concurrency & Safe Thread Offloading

18. **Non-Blocking Cooperative Execution**: Declare all I/O-bound repository protocols and router endpoints as `async def` coroutines. Never call synchronous blocking I/O (`time.sleep`, blocking sockets) on the event loop.
19. **Concurrent Task Orchestration (`asyncio.gather`)**: Orchestrate independent I/O tasks concurrently, reducing latency from $\mathcal{O}(\sum t_i)$ to $\mathcal{O}(\max(t_i))$. Wrap with exception handling to explicitly cancel pending sibling tasks on failure.
20. **Offloading CPU-Bound Work (`asyncio.to_thread`)**: Offload CPU-heavy computation (PBKDF2 key derivation, checksums, large data parsing) to worker threads, preserving sub-5ms `/health` probe responsiveness.
21. **Post-Response Background Processing (`BackgroundTasks`)**: Deliver HTTP responses in sub-35ms by queuing peripheral work (welcome emails, audit logging) in `BackgroundTasks`.
22. **Primitive-Only Background Task Invariant**: Never pass request-scoped dependencies (database sessions, transaction contexts) to background tasks. Pass only immutable primitives (`int`, `str`, `datetime`) or frozen DTOs to eliminate the Dependency Lifetime Trap.
23. **Defensive Background Exception Containment**: Wrap background task execution bodies in `try...except Exception:` with structured logging to prevent background failures from terminating the server process.

---

## 5. SQLAlchemy 2.0 Async Engine, Alembic & Unit of Work

24. **`expire_on_commit=False` Invariant**: Always configure `expire_on_commit=False` on `async_sessionmaker` to prevent `MissingGreenlet` exceptions during post-commit attribute access.
25. **Clean Lifespan Socket Draining**: Initialize schemas and call `await engine.dispose()` inside FastAPI's `@asynccontextmanager async def lifespan(app: FastAPI)` to guarantee zero socket leaks on shutdown.
26. **Optimistic Stale Connection Eviction (`pool_pre_ping=True`)**: Validate checked-out connections with `pool_pre_ping=True` to seamlessly discard dead sockets without throwing 500 errors to clients.
27. **Bounded Connection Pool Limits**: Restrict connection pool sizing (`pool_size=20`, `max_overflow=10`, `pool_recycle=1800`) to protect database connection limits under surge traffic.
28. **Isolated TestBase for Migrations**: Declare test-only ORM models on an isolated `TestBase`, keeping production `Base.metadata` clean for Alembic `compare_metadata` schema drift assertions.
29. **Dual-Mode Repository Providers**: Dependency providers return real `SqlAlchemyUserRepository(session)` during HTTP requests and accept `InMemoryUserRepository` for isolated, sub-millisecond testing.
30. **Zero ORM Leakage**: Concrete repositories map ORM models to slotted domain entities (`_to_entity()`) before returning. Raw SQLAlchemy models must never escape into services or routers.
31. **Eager Loading Optimization**: Use `selectinload()` for 1-to-many relationships (strictly 2 queries, zero $N+1$ and zero Cartesian bloat) and `joinedload()` for many-to-1 relationships (single SQL join).
32. **Defensive `lazy="raise"`**: Configure `lazy="raise"` on all SQLAlchemy declarative relationships to raise immediate exceptions on accidental lazy loading in async mode.
33. **The Unit of Work (UoW) Pattern**: Coordinate multi-repository transactions via `UnitOfWorkProtocol`. Repositories never call `commit()`; the UoW manages atomic commit, automatic rollback on error in `__aexit__`, and guaranteed session teardown in `finally:`.
34. **In-Memory UoW Snapshots**: Implement `InMemoryUnitOfWork` using shallow dictionary snapshots on `__aenter__` to authentically simulate ACID rollbacks in memory without a live database.

---

## 6. High-Performance Data Structures & Algorithmic Complexity

35. **Compact Hash Map Internals**: CPython's two-tier design (sparse index table pointing into dense entries array) minimizes memory overhead and guarantees insertion order.
36. **Open Addressing with Perturbation Probing**: Collision resolution utilizes `i = ((5 * i) + 1 + perturb) & mask` with power-of-2 tables to prevent primary clustering and guarantee full slot traversal.
37. **Tombstone Preservation (`DUMMY = -2`)**: Open-addressed table deletions write dummy tombstones to maintain active probe chains. Resizing triggers when load factor reaches 2/3.
38. **Slotted Domain Entities (`@dataclass(slots=True)`)**: Eliminate per-instance `__dict__` hash tables on domain entities, saving 40–60%+ RAM and turning attribute typos into immediate `AttributeError` exceptions. Slotted parent classes require child subclasses to also declare `slots=True`.
39. **Slotted Prefix Trie for Sub-Millisecond Autocomplete**: Replace $\mathcal{O}(N)$ SQL `LIKE '%term%'` scans with an in-memory `PrefixTrie` executing in strict $\mathcal{O}(k)$ time (where $k$ is prefix length).
40. **Recursive Bottom-Up Trie Pruning**: Trie word deletion recursively unlinks unreferenced leaf nodes when `len(node.children) == 0`, eliminating dead branch memory leaks.
41. **Binary Min-Heap (`heapq`) Priority Scheduling**: Priority job queues utilize Python's binary min-heap for strict $\mathcal{O}(\log N)$ enqueue and dequeue operations.
42. **Slotted Priority Job Invariants**: Decorate priority job models with `@dataclass(slots=True, order=True)`. Mark unorderable fields (`payload`, `job_id`) with `field(compare=False)` and include a monotonic integer `sequence: int` to guarantee deterministic FIFO tie-breaking without runtime type errors.
43. **Adaptive Non-Busy-Waiting Worker Sleep**: Schedulers inspect the head job with `peek_next_job()` ($\mathcal{O}(1)$) and sleep for the exact remaining delta (`scheduled_at - now`) rather than executing CPU-burning busy loops.
44. **Sliding Window Log Rate Limiting**: Implement rolling window rate limiting using slotted `collections.deque[float]`. Continuously evaluate requests within $[now - window, now]$, completely eliminating the 2x boundary burst defect of fixed-window counters.
45. **Amortized $\mathcal{O}(1)$ Stale Timestamp Eviction**: Purge expired timestamps from the front of the queue (`while queue and queue[0] <= threshold: queue.popleft()`).
46. **Active Zero-Leak Memory Sweeper**: Rate limiters periodically sweep inactive client keys exceeding `idle_seconds`, preventing unbounded dictionary memory growth from transient IPs.
47. **Binary Search for Ordered Intervals**: Logarithmic bisection (`binary_search_bounds`) filters pre-sorted data in $\mathcal{O}(\log N)$ time, bypassing sequential list scans.
48. **Converging Two-Pointer Optimization**: Identify target sum metric pairs in pre-sorted data using inward-converging pointers (`left`, `right`) terminating in $\mathcal{O}(N)$ time and $\mathcal{O}(1)$ space, completely eliminating quadratic $\mathcal{O}(N^2)$ nested loops.

---

## 7. Global ASGI Middleware & Centralized Error Handling

49. **ASGI Middleware Onion Wrapping**: Intercept HTTP traffic at the application perimeter using `BaseHTTPMiddleware` to guarantee 100% header coverage across all responses, including framework 404, 422, and 500 crashes.
50. **Monotonic Latency Tracking**: Measure request duration with `time.perf_counter()` to eliminate wall-clock NTP drift, injecting latency via `X-Process-Time-Ms`.
51. **Trace ID Parity**: Synchronize correlation IDs by storing them on `request.state.request_id` and injecting `X-Request-ID`. Global exception handlers read `getattr(request.state, "request_id", None)` to guarantee parity between response headers and JSON error bodies.
52. **Decoupled Domain Exceptions**: Define clean domain exceptions (`BaseDomainException`, `EntityNotFoundException`, `EntityConflictException`) in the domain layer. Routers and services remain transport-agnostic.
53. **Standardized Error Envelope**: Centralize all error responses (4xx, 5xx) under a predictable contract (`code`, `message`, `status_code`, `timestamp`, `trace_id`, `details`).
54. **Safe 500 Traceback Masking**: Catch-all 500 exception handlers securely log full stack traces internally with unique `trace_id` while returning a masked message to clients, preventing information leakage.

---

## 8. Testing Rigor, Async Mocking & Codebase Compliance

55. **Centralized `conftest.py` & Test Isolation**: Centralize fixtures with `autouse=True` teardowns using `yield` and `repo.clear()` to guarantee test independence without state pollution.
56. **Pytest-Asyncio Strict Mode**: Decorate all asynchronous fixtures with `@pytest_asyncio.fixture` in strict mode to guarantee proper event loop initialization and teardown.
57. **Async Test Doubles (`AsyncMock(spec=...)`)**: Instantiate asynchronous mocks using `unittest.mock.AsyncMock(spec=ProtocolClass)`. Never use synchronous `MagicMock` for async methods.
58. **Guaranteed Teardown for `app.dependency_overrides`**: Always wrap route dependency overrides in `try...finally:` or fixture yield teardowns (`app.dependency_overrides.pop(...)`) to eliminate inter-test pollution.
59. **Automated AST & Introspection Codebase Compliance**: Maintain automated tests (`tests/test_codebase_compliance.py`) using `ast` and `inspect` to verify 0 `print()` calls in `app/`, 100% explicit return annotations, and protocol completeness.
60. **Rust-Powered Linting (`ruff`)**: Enforce rules `E`, `W`, `F`, `I`, `UP`, `B`, `S`, and `T201` via `pyproject.toml` to guarantee modern syntax, import order, security posture, and zero production prints.

---

## 9. The 84 Month-1 Forbidden Anti-Patterns

| Category | Forbidden Anti-Patterns |
| :--- | :--- |
| **Architecture** | 1. Isolated topic folders (`day1/`, `day2/`)<br>2. Monolithic code slop (routes, DB, schemas in one file)<br>3. Direct DB operations in routers<br>4. Exposing raw ORM models or internal entities outside the repository layer<br>5. Domain services or repositories raising `HTTPException`<br>6. Independent, uncoordinated sessions in multi-entity workflows |
| **Validation & Schemas** | 7. Imperative parameter checks in router bodies instead of `Path()`/`Query()`<br>8. Cross-field validation in router/service bodies instead of `@model_validator`<br>9. Calling `self.model_dump()` inside validators<br>10. Persisting transient fields (`password_confirm`) to entities<br>11. String cleaning (`.strip()`) in routers/services<br>12. Re-compiling regex inside functions<br>13. $\mathcal{O}(n)$ list scans for blocked terms instead of `frozenset`<br>14. Mutable default arguments (`items: list = []`) |
| **Security & Auth** | 15. Ad-hoc header parsing in router bodies<br>16. Standard equality (`==`) for secrets instead of `secrets.compare_digest`<br>17. Reading environment variables inside request handlers<br>18. Literal routes declared below parameterized endpoints (`/me` below `/{id}`)<br>19. Imperative authorization checks in endpoint bodies instead of `Depends()`<br>20. Hardcoding role lists repeatedly across endpoints |
| **Lifecycle & Async** | 21. Omitting `finally:` in generator dependencies<br>22. Manual resource teardown in router endpoints<br>23. Blocking the event loop with synchronous sleep or socket I/O in coroutines<br>24. Sequential `await` loops for independent I/O instead of `asyncio.gather`<br>25. Unawaited coroutines or failing to cancel pending sibling tasks<br>26. Running CPU-bound tasks directly on the event loop without `to_thread`<br>27. Unsynchronized shared state mutations in worker threads<br>28. The Dependency Lifetime Trap (passing open sessions/requests to BackgroundTasks)<br>29. Unbounded in-memory collections causing OOM crashes<br>30. Blocking operations inside async background tasks |
| **Persistence & DB** | 31. Default `expire_on_commit=True` in async SQLAlchemy<br>32. Omitting `await engine.dispose()` on shutdown<br>33. Instantiating engines or sessions manually inside endpoints<br>34. Omitting `pool_pre_ping=True` in production<br>35. Unbounded connection pools<br>36. Leaking connection handles outside context managers<br>37. Subclassing production `Base` for test-only models<br>38. Relying on `Base.metadata.create_all()` in production lifespan instead of Alembic<br>39. In-memory pagination over relational queries<br>40. The $N+1$ query problem (missing eager loading)<br>41. Cartesian explosion from `joinedload` on one-to-many collections<br>42. Repositories committing transactions individually |
| **DSA & Memory** | 43. Naive linear probing causing primary clustering<br>44. Unbounded load factors (> 2/3) in custom hash tables<br>45. Empty slot deletion breaking open-addressing probe chains (missing tombstones)<br>46. Unslotted domain entities incurring dynamic `__dict__` overhead<br>47. Silently reintroducing `__dict__` via unslotted inheritance<br>48. Dynamic ad-hoc attribute monkeypatching on domain entities<br>49. Using SQL `LIKE '%term%'` for real-time autocomplete<br>50. Unpruned in-memory tree nodes leaking memory<br>51. Using sorted lists for priority queues ($\mathcal{O}(N)$ insertions)<br>52. Comparing unorderable dataclass payload fields in heapq<br>53. CPU busy-waiting spinloops in polling schedulers<br>54. Fixed-window rate limiters vulnerable to 2x boundary bursts<br>55. Unbounded timestamp lists for sliding windows<br>56. Omitting idle client sweeps in memory limiters<br>57. $\mathcal{O}(N)$ linear scanning over pre-sorted data<br>58. $\mathcal{O}(N^2)$ nested loops for pair matching |
| **Middleware & Errors** | 59. Per-route security header or latency injection anti-pattern<br>60. Desynchronized trace IDs between error body and response headers<br>61. Leaking internal stack traces or DB details in 500 error responses<br>62. Inconsistent error response envelopes |
| **Testing & Tooling** | 63. Using synchronous `MagicMock` on coroutines<br>64. Hitting real external services or live databases in unit tests<br>65. Inter-test state pollution from un-cleared repositories<br>66. Using standard `@pytest.fixture` on async fixtures in strict mode<br>67. Self-referential dependency overrides causing recursion<br>68. Using `Any` as a type safety shortcut<br>69. Untyped function signatures in production code<br>70. Suppressing type errors via `# type: ignore` workarounds<br>71. Leaving `print()` debugging statements in production code |

---

## 10. Architectural Inheritance Contract for Month 2

All upcoming distributed systems engineering in Month 2 seamlessly inherits and builds on top of this sealed single-node foundation:
1. **Redis Caching (Days 61–63)**: Respects Clean Architecture; caching layers implement repository protocols and wrap persistent repositories without leaking Redis types into services.
2. **Distributed Rate Limiting (Day 55)**: Upgrades our slotted in-memory sliding window log algorithm to Redis Lua scripts while maintaining the exact same DTO boundaries and RFC `Retry-After` header contracts.
3. **Authentication & JWT Hardening (Days 46–50)**: Builds directly on our constant-time verification (`secrets.compare_digest`), declarative `Depends()` guards, and sub-dependency chaining.
4. **Relational Query Optimization (Days 31–45)**: Extends our async engine pooling, `selectinload` vs `joinedload` rules, and UoW patterns into complex association tables, keyset pagination, and full-text search.
5. **Zero-Tolerance Quality Gates**: All PRs and new features in Month 2 must continue to pass `mypy --strict`, `ruff check`, and 100% automated test suites before being merged.
