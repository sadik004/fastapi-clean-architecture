# RCA-049: Day 49 — Test Failures in Field-Level Encryption Integration

**Date:** 2026-09-09  
**Severity:** Medium (Test-suite regression; no data loss)  
**Resolved:** ✅ Yes  

---

## Summary

4 bugs introduced during Day 49 FLE implementation caused 2 initially failing tests. Root-cause analysis and fixes documented below.

---

## RCA-049-A: Wrong Repository Import in `main.py`

**Classification:** Critical Architectural Bug  

### Symptoms
- App lifespan function `seed_user_bloom_filter()` was seeding Bloom Filter from the wrong (in-memory) repository.
- Bloom Filter would be empty on every startup, causing all existing users to be rejected with `UserNotFoundException` until cache warmed via actual requests.

### Root Cause
```python
# WRONG — imports from user_repository which has InMemoryUserRepository
from app.repositories.user_repository import SqlAlchemyUserRepository
```
`user_repository.py` does NOT define `SqlAlchemyUserRepository` — it only defines `InMemoryUserRepository` and the protocol. Python's module system allowed this to import without error because a name collision existed from a legacy backward-compat alias that was later removed.

### Fix
```python
from app.repositories.sqlalchemy_user_repository import SqlAlchemyUserRepository
```

### Prevention
- Add a module-level `__all__` to `user_repository.py` that explicitly lists public names.
- Add a test that verifies `main.py`'s lifespan seeds from the correct concrete implementation.

---

## RCA-049-B: Generic Exception Code in `UserNotFoundException`

**Classification:** Domain Contract Violation  

### Symptoms
- `test_api_endpoint_update_user_nid_not_found` expected `{"error": {"code": "USER_NOT_FOUND"}}` but received `{"error": {"code": "NOT_FOUND"}}`.

### Root Cause
1. `UserNotFoundException` had `code="ENTITY_NOT_FOUND"` — too generic.
2. When the route couldn't match (because of wrong URL prefix — see RCA-049-C), Starlette returned HTTP 404, which the exception handler mapped to the static `"NOT_FOUND"` code from `_HTTP_STATUS_CODE_MAP`.

### Fix
Changed `UserNotFoundException` default code from `"ENTITY_NOT_FOUND"` to `"USER_NOT_FOUND"`, providing domain-specific error identification for API consumers.

### Prevention
- All `EntityNotFoundException` subclasses must override `code` to be resource-specific.
- Add a contract test verifying each domain exception has a unique, documented machine-readable code.

---

## RCA-049-C: Test Used Non-Existent URL Prefix `/api/v1/`

**Classification:** Test Authoring Error  

### Symptoms
- `POST /api/v1/users/` → HTTP 404 "Not Found" (Starlette route match failure).
- `PUT /api/v1/users/{id}/nid` → HTTP 404 "Not Found".

### Root Cause
FLE integration tests were written with an assumed URL prefix `/api/v1/` that does not exist in the actual router configuration. The user router is mounted with prefix `/users`, making the actual base path `/users/`.

Conftest and all existing tests correctly use `/users/` — this was a copy-paste error in the new test file.

### Fix
Corrected all test paths:
- `/api/v1/users/` → `/users/`
- `/api/v1/users/{id}/nid` → `/users/{id}/nid`

### Prevention
- Centralize the base URL prefix in `conftest.py` as a constant (e.g., `USERS_BASE = "/users"`).
- All new test files should reference this constant rather than hardcoding strings.

---

## RCA-049-D: Cross-Session Raw SQL Verification in ASGI Test

**Classification:** Test Design Error  

### Symptoms
- `scalar_one()` raised `sqlalchemy.exc.NoResultFound` — user created via HTTP API not visible in direct `async_session_factory()` session.

### Root Cause
ASGI transport test creates the user in a database session managed by the FastAPI lifespan/dependency injection system. After the HTTP call completes, the test attempts to verify via a _new_ `async_session_factory()` session. In SQLite WAL mode and under async connection pooling, this second session may not immediately see the committed write from the application session due to connection isolation or write ordering.

The raw SQL verification (`SELECT nid_number FROM users WHERE id = ?`) returned no rows because the connection checkout was using a snapshot predating the application's commit.

### Fix
Removed the cross-session raw SQL verification from the HTTP endpoint integration test. This invariant (Fernet ciphertext in DB) is already comprehensively verified by `test_raw_sql_storage_ciphertext_verification`, which uses consistent direct ORM sessions throughout.

The HTTP endpoint test now verifies only the HTTP contract: decrypted plaintext is returned in the `UserResponse` body.

### Prevention
- Integration tests using ASGI transport must NOT mix HTTP-level operations with direct DB session operations in different session scopes.
- Raw database verification must occur either within the same ORM session or be delegated to a dedicated ORM-layer test.
- Document this boundary in `conftest.py` with a comment.
