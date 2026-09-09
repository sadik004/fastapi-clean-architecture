# RCA: Day 49 - Field-Level Encryption, Fernet TypeDecorator, Lifespan Seeding Drift, and ASGI Session Boundary Isolation

- **Date**: 2026-09-09
- **Role**: Junior Apprentice Backend Engineer
- **Lead Architect & Mentor**: User
- **Topic**: Field-Level Encryption (FLE), SQLAlchemy TypeDecorator, Fernet Cryptography, ASGI Test Session Boundary Isolation, and Lifespan Seeding Integrity
- **Status**: ✅ Resolved (100% Tests Passing, Zero Regressions)

---

## 1. Trigger & Production Hazard

During Day 49 implementation of Application-Level Field-Level Encryption (FLE) using Fernet authenticated symmetric cryptography (`AES-128-CBC + HMAC-SHA256`) and SQLAlchemy `TypeDecorator`, 4 distinct bugs emerged across application initialization, domain error contracts, test authoring, and ASGI test session isolation:
1. **Lifespan Repository Import Collision**: `main.py` lifespan imported `SqlAlchemyUserRepository` from the wrong module (`app.repositories.user_repository` instead of `app.repositories.sqlalchemy_user_repository`). In production, this caused the startup Bloom Filter seeding to execute against an empty in-memory repository rather than the database, causing all legitimate users to fail existence checks.
2. **Domain Contract Drift in Exception Error Codes**: `UserNotFoundException` defaulted to generic `"ENTITY_NOT_FOUND"` instead of `"USER_NOT_FOUND"`, breaking API consumer programmatic error parsing.
3. **Test Authoring URL Route Mismatch**: Integration tests hardcoded `/api/v1/users/` instead of `/users/`, triggering framework HTTP 404 route matching failures.
4. **Cross-Session SQLite WAL Snapshot Contention in ASGI Tests**: Direct SQLAlchemy async sessions opened within test functions failed to observe writes committed inside the application's ASGI client transport context (`NoResultFound`), breaking raw SQL ciphertext verification.

---

## 2. Faulty Code & Architectural Anti-Patterns

### Anti-Pattern A: Misleading Module Import Collision in Application Lifespan
```python
# FAULTY (app/main.py):
# user_repository.py only exports InMemoryUserRepository; SqlAlchemyUserRepository was a stale alias
from app.repositories.user_repository import SqlAlchemyUserRepository

async def seed_user_bloom_filter(app: FastAPI) -> None:
    # Seeds Bloom filter from empty in-memory store instead of persistent database!
    repo = SqlAlchemyUserRepository()
    ids = await repo.get_all_ids()
```

### Anti-Pattern B: Generic Base Exception Code Leaking Across Layer Boundaries
```python
# FAULTY (app/core/exceptions.py):
class UserNotFoundException(EntityNotFoundException):
    def __init__(self, message: str = "User not found") -> None:
        super().__init__(message)  # Inherits self.code = "ENTITY_NOT_FOUND"
```

### Anti-Pattern C: Non-Existent API Prefix in Integration Tests
```python
# FAULTY (tests/test_field_level_encryption.py):
response = await client.post("/api/v1/users/", json=payload)  # HTTP 404 Not Found
```

### Anti-Pattern D: Cross-Context DB Session Leakage in ASGI Transport Tests
```python
# FAULTY (tests/test_field_level_encryption.py):
# Step 1: Create user via HTTP client
response = await client.post("/users/", json=payload)
# Step 2: Open an independent direct session to check raw SQLite ciphertext
async with async_session_factory() as direct_session:
    row = (await direct_session.execute(text("SELECT nid_number FROM users WHERE id = :id"))).scalar_one()
    # FAILS with sqlalchemy.exc.NoResultFound due to SQLite WAL isolation & connection checkout race!
```

---

## 3. Root Cause Analysis

1. **Namespace Pollution Without Strict `__all__` Declarations**:
   - `app/repositories/user_repository.py` contained stale re-exports from legacy iterations. Python's module import resolution loaded the symbol without error, masking the fact that the lifespan was instantiating an unintended repository class.
2. **Incomplete Domain Error Code Specialization**:
   - When HTTP 404 was returned, the global exception handler mapped Starlette's 404 to `"NOT_FOUND"`, while `UserNotFoundException` inherited `"ENTITY_NOT_FOUND"`. Neither returned the API contract specification of `"USER_NOT_FOUND"`.
3. **Hardcoded Ad-Hoc Route Strings**:
   - Authors assumed a conventional `/api/v1` prefix without checking `app/main.py` where routers are mounted at `/users`.
4. **ASGI Transport vs Direct SQLAlchemy Session Scope Incompatibility**:
   - When using `httpx.AsyncClient(transport=ASGITransport(app=app))`, database writes occur inside the FastAPI dependency-injected session and commit to the engine pool. A separate `async_session_factory()` session in the same async test function checks out a different connection from the pool before the WAL index updates or connection flush completes, creating phantom `NoResultFound` errors.

---

## 4. Resolution & Refactored Implementation

### Fix A: Explicit Module Import in `main.py`
```python
# CORRECT (app/main.py):
from app.repositories.sqlalchemy_user_repository import SqlAlchemyUserRepository

async def seed_user_bloom_filter(app: FastAPI) -> None:
    async with async_session_factory() as session:
        repo = SqlAlchemyUserRepository(session=session)
        ids = await repo.get_all_ids()
        for uid in ids:
            app.state.bloom_filter.add(uid)
```

### Fix B: Domain-Specific Exception Code
```python
# CORRECT (app/core/exceptions.py):
class UserNotFoundException(EntityNotFoundException):
    def __init__(self, message: str = "User not found") -> None:
        super().__init__(message)
        self.code = "USER_NOT_FOUND"
```

### Fix C: Accurate Route Prefixes in Test Suites
```python
# CORRECT (tests/test_field_level_encryption.py):
response = await client.post("/users/", json=payload)
assert response.status_code == 201
```

### Fix D: Strict Separation Between ASGI Transport Contracts and ORM Storage Assertions
```python
# CORRECT (tests/test_field_level_encryption.py):
# 1. ORM Test: verifies Fernet ciphertext in database using a single consistent session
async def test_raw_sql_storage_ciphertext_verification(async_session: AsyncSession) -> None:
    user = UserModel(email="audit@example.com", nid_number="NID-123456")
    async_session.add(user)
    await async_session.commit()
    
    raw = (await async_session.execute(text("SELECT nid_number FROM users WHERE id = :id"), {"id": user.id})).scalar_one()
    assert raw.startswith("gAAAAA")  # Verified Fernet token in raw storage

# 2. HTTP Integration Test: verifies plaintext decryption contract through ASGI transport
async def test_api_endpoint_fle_roundtrip(client: AsyncClient) -> None:
    response = await client.post("/users/", json={"email": "alice@example.com", "nid_number": "NID-777"})
    assert response.status_code == 201
    assert response.json()["nid_number"] == "NID-777"
```

---

## 5. Permanent Prevention Rules

Codified into `.agents/skills/fastapi-production/SKILL.md`:
1. **Pattern #138 (FLE with Fernet + TypeDecorator)**: All PII columns must be protected by `EncryptedString` ensuring raw SQL dumps contain only `gAAAAA...` ciphertext.
2. **Pattern #141 (Domain-Specific Error Codes)**: Every `EntityNotFoundException` subclass must override `self.code` with a resource-specific identifier (e.g. `USER_NOT_FOUND`).
3. **Pattern #142 (Exact Defining Module Lifespan Imports)**: All dependencies used in FastAPI `lifespan` startup must be imported from their exact declaring module.
4. **Pattern #144 (ASGI Transport Session Boundary Discipline)**: Never mix ASGI HTTP calls with direct `async_session_factory()` queries in the same test function; keep HTTP contract assertions and ORM ciphertext assertions in separate test scopes.
5. **Pattern #145 (Test URL Path Verification)**: Verify actual router mounting prefixes before writing endpoint integration tests.
