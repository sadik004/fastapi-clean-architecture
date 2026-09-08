# Day 03: Complete CRUD Lifecycle, O(1) In-Memory Repository & FastAPI Dependency Injection

**Date**: 2026-09-08  
**Role**: Junior Apprentice Backend Engineer  
**Lead Architect & Mentor**: User

---

## 1. Concepts Covered Today
- **Repository Protocol Abstraction**: Defined `UserRepositoryProtocol` using `typing.Protocol` to decouple the service and router layers from the concrete in-memory storage implementation.
- **Complete In-Memory CRUD Lifecycle**:
  - `create()`: $\mathcal{O}(1)$ primary storage insertion and index registration.
  - `get_by_id()`, `get_by_email()`, `get_by_username()`: $\mathcal{O}(1)$ hash map lookups.
  - `update()`: $\mathcal{O}(1)$ entity update with bidirectional index synchronization.
  - `delete()`: $\mathcal{O}(1)$ deletion with complete purging of secondary indexes.
  - `list_all(limit, offset)`: Slice-based pagination in $\mathcal{O}(k)$ time where $k = \text{limit}$.
- **Strict Secondary Index Synchronization & Purging**:
  - Updating email or username immediately evicts the old key from `_email_index` or `_username_index` and maps the new key.
  - Deleting a user immediately purges both `_email_index` and `_username_index`, preventing memory leaks and ghost references, and ensuring subsequent re-registration with freed keys succeeds without false collision errors.
- **FastAPI Dependency Injection (`Depends`)**:
  - Wired `get_user_repository() -> UserRepositoryProtocol` to supply the abstract protocol.
  - Injected repository into `UserService`, and `UserService` into thin router endpoints.
- **RESTful Endpoint Standards**:
  - `POST /users/`: HTTP 201 Created.
  - `GET /users/{user_id}`: HTTP 200 OK or 404 Not Found.
  - `GET /users/`: HTTP 200 OK with `limit` and `offset` query parameter validation.
  - `PUT /users/{user_id}`: HTTP 200 OK, 404 Not Found, or 409 Conflict.
  - `DELETE /users/{user_id}`: HTTP 204 No Content with empty response body.

---

## 2. Architectural Decisions Made
- **Protocol-First Service Contract**: `UserService` accepts `UserRepositoryProtocol` rather than `InMemoryUserRepository`, enabling swapping the backing store for PostgreSQL/SQLAlchemy in Phase 3 without altering a single line of business logic.
- **Encapsulated Index Management**: The dictionary stores (`_store`, `_email_index`, `_username_index`) are strictly private to `InMemoryUserRepository`. The service layer only orchestrates domain rules via public protocol methods.
- **Idempotent / Conflict-Safe Updates**: Uniqueness checks for modified fields explicitly ignore the current entity's existing ID, preventing false 409 conflicts on self-updates.

---

## 3. DSA Time & Space Complexity Enforced
- **Primary Lookup (`get_by_id`)**: $\mathcal{O}(1)$ time complexity using hash map key access.
- **Index Lookups (`get_by_email`, `get_by_username`)**: $\mathcal{O}(1)$ time complexity via inverted index dictionaries.
- **Entity Creation & Deletion**: $\mathcal{O}(1)$ amortized time for primary dictionary and secondary index mutations.
- **Index Purging on Deletion**: Strictly $\mathcal{O}(1)$ operations (`pop(key, None)`), avoiding any $\mathcal{O}(n)$ scans or lingering memory leaks.
- **Paginated Listing**: $\mathcal{O}(\text{offset} + \text{limit})$ slicing.

---

## 4. Summary of Test Results & Quality Gates
- **Pytest**: 54 passed in 0.55s (`100%` pass rate).
  - `tests/test_user_repository.py`: 7 tests verifying CRUD, index migration on update, index purging on deletion, and pagination.
  - `tests/test_user_crud.py`: 5 comprehensive integration tests verifying the full lifecycle (`POST -> GET -> PUT -> DELETE -> GET 404 -> Re-register 201`), 409 conflict scenarios, 404 handling, and paginated queries.
  - `tests/test_schemas.py`: 25 tests passing.
  - `tests/test_user.py`: 17 tests passing.
- **Mypy**: `Success: no issues found in 17 source files` with `mypy --strict app tests`.
- **Ruff**: `All checks passed!` across `app/` and `tests/`.

---

## 5. Suggested Git Commit
```bash
git commit -m "feat(day-03): implement full CRUD repository protocol with O(1) index synchronization and dependency injection"
```
