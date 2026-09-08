# Day 20: The Unit of Work (UoW) Pattern (Atomic ACID Transactions Across Multiple Repositories)

**Date**: 2026-09-08  
**Role**: Junior Apprentice Backend Engineer  
**Lead Architect & Mentor**: User

---

## 1. Concepts Covered Today
- **The Unit of Work (UoW) Pattern**:
  - Implemented `UnitOfWorkProtocol` in `app/core/unit_of_work.py` (re-exported in `app/repositories/unit_of_work.py`), decoupling multi-repository transactional orchestration from persistence engines.
  - Stripped transaction commits from individual repositories; repositories now only stage operations (`add()`, `flush()`, `refresh()`, or queries), while the Unit of Work exclusively controls transaction boundaries (`commit()`, `rollback()`).
- **Production `SqlAlchemyUnitOfWork`**:
  - In `__aenter__`: Acquires a single `AsyncSession` from `async_sessionmaker` and initializes both `SqlAlchemyUserRepository(session=self.session)` and `SqlAlchemyPostRepository(session=self.session)` sharing the **EXACT SAME** underlying session.
  - In `__aexit__`: Automatically executes `await self.rollback()` on any unhandled exception (`exc_type is not None`) and unconditionally closes `self.session` in a `finally:` block, guaranteeing zero connection pool leaks.
  - Enforced runtime access guards: Attempting to access `uow.users` or `uow.posts` outside an active `async with uow:` block raises `RuntimeError`.
- **Fast, Isolated In-Memory Unit of Work (`InMemoryUnitOfWork`)**:
  - Built `InMemoryPostRepository` implementing `PostRepositoryProtocol`.
  - Implemented `InMemoryUnitOfWork` utilizing dictionary and index snapshots (`_store`, `_email_index`, `_username_index`, `_current_id`).
  - Upon exception or explicit `await uow.rollback()`, state is restored to pre-context snapshots, enabling high-speed unit tests with true ACID rollback semantics.
- **Atomic Multi-Entity Business Service (`create_user_with_initial_post`)**:
  - Implemented `UserService.create_user_with_initial_post(...)` orchestrating user registration and initial post creation within `async with uow:`.
  - Guaranteed complete rollback: If post creation or subsequent validation fails, user registration is rolled back, leaving zero orphaned records.
- **Dependency Injection & API Routing**:
  - Added `get_uow() -> UnitOfWorkProtocol` dependency provider in `app/core/dependencies.py` and wired into `get_user_service`.
  - Added `POST /users/with-initial-post` endpoint in `app/routers/user_router.py`, handling atomic onboarding and returning HTTP 201 with `UserWithInitialPostResponse`.

---

## 2. DSA Time & Space Complexity Enforced
- **Multi-Repository Staged Creation**:
  - Time Complexity: $\mathcal{O}(1)$ clustered index insertions for user and post within the active transaction buffer.
  - Space Complexity: Strictly $\mathcal{O}(1)$ memory allocation for staged entity models and detached response DTOs.
- **In-Memory Snapshot & Rollback Simulation**:
  - Time Complexity: $\mathcal{O}(N)$ shallow dictionary copying during context enter/rollback where $N$ is active entity count.
  - Space Complexity: $\mathcal{O}(N)$ temporary snapshot references discarded upon commit or exit.
- **ACID Atomicity Invariant**:
  - Either BOTH user and post are persisted to the database, or NEITHER exists. Zero partial writes or orphaned records permitted.

---

## 3. Summary of Test Results & Quality Gates
- **Pytest Suite**: **287 passed** in 17.70s (`100%` pass rate across 35 test modules).
  - `tests/test_unit_of_work.py`: 9 new comprehensive tests passing:
    1. `test_uow_atomic_commit_persists_user_and_post`: Verifies user and post persist atomically on commit.
    2. `test_uow_rollback_on_exception_leaves_zero_orphaned_records`: Proves that a simulated failure during post creation completely rolls back the user record.
    3. `test_uow_explicit_rollback_discards_staged_changes`: Verifies explicit `await uow.rollback()` discards all staged entities.
    4. `test_uow_session_cleanup_and_access_guards`: Verifies session is closed in `finally:` on both normal and exception exits, and access outside context is blocked.
    5. `test_in_memory_uow_snapshot_and_rollback`: Verifies in-memory dictionary snapshot and index restoration on rollback.
    6. `test_service_create_user_with_initial_post_atomic_success`: Verifies service-level orchestration succeeds atomically.
    7. `test_service_create_user_with_initial_post_duplicate_email_rejected`: Verifies duplicate email rejection within UoW block.
    8. `test_endpoint_create_user_with_initial_post_success`: Validates `POST /users/with-initial-post` HTTP 201 response and schema.
    9. `test_endpoint_create_user_with_initial_post_duplicate_returns_409`: Validates 409 Conflict handling without orphan records.
- **Strict Type Checking (`mypy --strict app tests alembic`)**:
  - `Success: no issues found in 53 source files`.
- **Linter & Formatting (`ruff check app tests alembic`)**:
  - `All checks passed!`.
