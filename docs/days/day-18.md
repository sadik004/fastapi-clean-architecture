# Day 18: The Repository Pattern (SQLAlchemy 2.0 Async Repository & Domain Entity Decoupling)

**Date**: 2026-09-08  
**Role**: Junior Apprentice Backend Engineer  
**Lead Architect & Mentor**: User

---

## 1. Concepts Covered Today
- **The Repository Pattern in Clean Architecture**:
  - Maintained `UserRepositoryProtocol` in `app/repositories/user_repository.py` as an abstract boundary protocol decoupled from specific storage engines.
  - Implemented `SqlAlchemyUserRepository` in `app/repositories/sqlalchemy_user_repository.py` using asynchronous SQLAlchemy 2.0 and `AsyncSession`.
- **The Zero ORM Leakage Boundary (`_to_entity`)**:
  - Established a strict architectural invariant: raw SQLAlchemy ORM entities (`UserModel`) never cross the repository boundary into `UserService` or routers.
  - Built private mapper `_to_entity(model: UserModel) -> UserEntity` inside `SqlAlchemyUserRepository`.
  - Guarantees that the domain and transport layers interact strictly with pure, detached Python dataclass `UserEntity` instances, eliminating `MissingGreenlet` and `DetachedInstanceError` risks.
- **Zero-Code-Change Service Persistence Transition**:
  - Updated `get_user_repository` dependency in `app/core/dependencies.py` to inject `session: Annotated[Optional[AsyncSession], Depends(get_db_session)] = None` and yield `SqlAlchemyUserRepository(session=session)`.
  - Proven that `UserService` required **ZERO lines of code changed** to switch from in-memory dictionary storage to a persistent relational SQL database, exemplifying the Dependency Inversion Principle (DIP).
- **Dual-Mode Dependency Injection & In-Memory Test Mocking**:
  - Preserved the ability to run ultra-fast isolated unit tests using `InMemoryUserRepository` via `app.dependency_overrides[get_user_repository] = lambda: _user_repository` in `tests/conftest.py`.
  - Enabled end-to-end API testing against real database tables by simply popping the dependency override.
- **Synchronous Test State Sanitation Contract**:
  - Extended `tests/conftest.py` autouse fixture `clean_repo` with `_clean_database()` to execute `DELETE FROM users` between test runs, eliminating inter-test state collisions.

---

## 2. DSA Time & Space Complexity Enforced
- **Clustered Primary Key Lookups (`get_by_id`)**:
  - Time Complexity: Strictly $\mathcal{O}(1)$ clustered index lookup in SQLite/PostgreSQL.
  - Space Complexity: Fixed $\mathcal{O}(1)$ memory allocation for returned `UserEntity`.
- **Unique B-Tree Secondary Index Lookups (`get_by_email`, `get_by_username`)**:
  - Time Complexity: Strictly $\mathcal{O}(\log N)$ binary tree traversal over indexed columns.
- **Database Engine-Level Pagination (`list_all`)**:
  - Time Complexity: $\mathcal{O}(\text{offset} + \text{limit})$ at the database engine level using SQL `LIMIT` and `OFFSET`.
  - Space Complexity: Strictly bounded $\mathcal{O}(\text{limit})$ memory allocation in Python heap, avoiding in-memory list buffering.
- **Entity Detachment & Mapping (`_to_entity`)**:
  - Time Complexity: Strictly $\mathcal{O}(1)$ field-by-field copy from ORM model to dataclass entity.

---

## 3. Summary of Test Results & Quality Gates
- **Pytest Suite**: **274 passed** in 16.90s (`100%` pass rate across 33 test modules).
  - `tests/test_sqlalchemy_user_repository.py`: 9 new tests passing:
    1. `test_sqlalchemy_repo_create_and_get_by_id`: Validates asynchronous entity insertion, auto-increment ID, server default timestamps, and retrieval.
    2. `test_sqlalchemy_repo_update_selective_fields`: Validates keyword-based and `UserUpdate` DTO updates.
    3. `test_sqlalchemy_repo_update_non_existent`: Validates `None` return for non-existent IDs.
    4. `test_sqlalchemy_repo_delete_lifecycle`: Validates entity removal and read-after-delete returning `None`.
    5. `test_sqlalchemy_repo_delete_non_existent`: Validates `False` return when deleting non-existent IDs.
    6. `test_sqlalchemy_repo_index_lookups`: Validates $\mathcal{O}(\log N)$ lookups by email and username.
    7. `test_sqlalchemy_repo_pagination_and_filtering`: Validates SQL-level `limit`, `offset`, `role`, and `search` query parameters.
    8. `test_sqlalchemy_repo_zero_orm_leakage_boundary`: Validates `UserModel` detachment and access outside active sessions.
    9. `test_end_to_end_api_crud_with_database`: Validates full FastAPI HTTP CRUD endpoints operating against real SQL database via `SqlAlchemyUserRepository`.
- **Strict Type Checking (`mypy --strict app tests alembic`)**:
  - `Success: no issues found in 45 source files`.
- **Linter & Formatting (`ruff check app tests alembic`)**:
  - `All checks passed!`.
