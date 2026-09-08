# Day 15: SQLAlchemy 2.0 Async Setup (create_async_engine, async_sessionmaker & Lifespan Management)

**Date**: 2026-09-08  
**Role**: Junior Apprentice Backend Engineer  
**Lead Architect & Mentor**: User

---

## 1. Concepts Covered Today
- **Centralized Database Settings (`app/core/config.py`)**:
  - Added database connectivity configuration to `Settings` using Pydantic Settings:
    - `database_url: str = "sqlite+aiosqlite:///./app.db"` (configurable to PostgreSQL `postgresql+asyncpg://...` via `.env`).
    - `db_echo: bool = False` (SQL query logging flag for debugging and performance auditing).
- **Modern SQLAlchemy 2.0 Async Engine (`app/core/database.py`)**:
  - Initialized `create_async_engine(settings.database_url, echo=settings.db_echo, connect_args=...)`.
  - Dynamically configured `connect_args={"check_same_thread": False}` when SQLite is in use to support concurrent event loop access.
- **Modern Declarative Base (`app/core/database.py`)**:
  - Implemented `class Base(AsyncAttrs, DeclarativeBase): pass` using modern SQLAlchemy 2.0 inheritance.
  - `AsyncAttrs` provides explicit async attribute loading support, preventing unexpected greenlet errors when navigating ORM relationships.
- **`async_sessionmaker` & The Mandatory `expire_on_commit=False` Invariant**:
  - Configured `async_session_factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False, autoflush=False)`.
  - **CRITICAL ARCHITECTURAL RULE**: In SQLAlchemy 2.0 Async mode, accessing entity attributes after a commit when `expire_on_commit=True` triggers implicit synchronous lazy loading, raising `sqlalchemy.exc.MissingGreenlet: greenlet_spawn has not been spawned; can't call await_only() here`. Setting `expire_on_commit=False` keeps committed entity attributes loaded in memory, eliminating runtime greenlet crashes.
- **Transactional Session Dependency with `yield` (`get_db_session`)**:
  - Implemented `get_db_session() -> AsyncGenerator[AsyncSession, None]`:
    - **Pre-yield**: Opens an isolated `AsyncSession` from `async_session_factory` in $\mathcal{O}(1)$ time.
    - **Post-yield**: Automatically commits on successful completion, issues `await session.rollback()` on exceptions, and guarantees `await session.close()` in `finally:` to eliminate connection leaks.
    - Re-exported via `app/core/dependencies.py` for standard FastAPI dependency injection.
- **FastAPI Lifespan Engine Management (`app/main.py`)**:
  - Configured `@asynccontextmanager async def lifespan(app: FastAPI)`:
    - **Startup**: Ensures relational tables exist via `await conn.run_sync(Base.metadata.create_all)` on `engine.begin()`.
    - **Shutdown**: Executes `await engine.dispose()` to cleanly terminate all pooled socket connections and prevent dangling connections on container restart or server reload.
- **Database Connectivity Verification Endpoint (`GET /health/db`)**:
  - Added a dedicated health check executing `await session.scalar(select(1))`.
  - Measures high-precision round-trip query ping latency using `time.perf_counter()`.
  - Returns `{"status": "healthy", "database": "connected", "latency_ms": <float>, "scalar_result": 1}`.

---

## 2. Architectural Decisions Made
- **Strict Layering & Separation of Concerns**:
  - Engine instantiation, declarative base, session factories, and session generators reside exclusively in `app/core/database.py`.
  - Routers, services, and domain layers NEVER instantiate engines or sessionmakers directly; all database access is injected via `Depends(get_db_session)`.
- **Defensive Error Handling in Session Lifecycle**:
  - In `get_db_session`, any unhandled exception automatically issues `await session.rollback()` before re-raising, guaranteeing transactional atomicity and preventing half-committed state from persisting.
- **Explicit Invariant Verification**:
  - Created contrast unit tests proving that while `expire_on_commit=False` safely allows post-commit attribute reading, an intentionally misconfigured `expire_on_commit=True` triggers `MissingGreenlet`.

---

## 3. DSA Time & Space Complexity Enforced
- **Session Allocation & Teardown**:
  - Time Complexity: Strictly $\mathcal{O}(1)$ to allocate a session from the connection pool and $\mathcal{O}(1)$ to close and return it.
  - Space Complexity: Strictly bounded $\mathcal{O}(1)$ overhead per active HTTP request.
- **Post-Commit In-Memory Attribute Access**:
  - Time Complexity: Strictly $\mathcal{O}(1)$ memory lookup since `expire_on_commit=False` retains entity attribute values in the Python object dictionary without triggering additional $\mathcal{O}(I/O)$ database round-trips.

---

## 4. Summary of Test Results & Quality Gates
- **Full Pytest Suite**: 256 passed in 12.23s (`100%` pass rate across 30 test modules).
  - `tests/test_database_async_setup.py`: 7 tests passing:
    1. `test_database_connection_ping`: Validates `select(1)` scalar evaluation via async session.
    2. `test_expire_on_commit_false_invariant`: Validates post-commit attribute retention without greenlet error.
    3. `test_expire_on_commit_true_demonstrates_missing_greenlet`: Validates that `expire_on_commit=True` raises `MissingGreenlet`.
    4. `test_get_db_session_auto_commits_on_clean_exit`: Validates auto-commit on normal generator exit.
    5. `test_get_db_session_rolls_back_on_exception`: Validates automatic rollback on unhandled exception.
    6. `test_health_db_endpoint_success`: Validates `GET /health/db` returns HTTP 200 with latency metrics.
    7. `test_lifespan_lifecycle_execution`: Validates lifespan table creation and `engine.dispose()`.
  - All 249 existing tests continue to pass without regression.
- **Strict Type Checking (`mypy --strict app tests`)**:
  - `Success: no issues found in 37 source files`.
- **Linter & Formatting (`ruff check app tests`)**:
  - `All checks passed!`.
- **RCA Documentation**:
  - Logged `docs/rca/day-15_pytest_asyncio_strict_fixture_and_missing_greenlet_invariant.md` and updated `docs/rca/README.md`.
