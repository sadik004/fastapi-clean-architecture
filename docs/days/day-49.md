# Day 49: Application-Level Field-Level Encryption (FLE) Architecture

**Date:** 2026-09-09  
**Topic:** Field-Level Encryption with Fernet Cryptography & SQLAlchemy TypeDecorator  
**Status:** ✅ Completed | 12/12 Tests Passing

---

## 🎯 Lesson Objective

Engineer an enterprise-grade **Field-Level Encryption (FLE)** system to protect PII (Personally Identifiable Information) against database breaches, SQL dump leaks, and insider threats — using **Fernet** authenticated symmetric cryptography and a transparent SQLAlchemy **TypeDecorator**.

---

## 🔐 Why Field-Level Encryption?

| Threat Model | TDE (Disk Encryption) | FLE (Application-Level) |
|---|---|---|
| Disk theft / cold storage | ✅ Protected | ✅ Protected |
| Compromised DBA with SQL access | ❌ Exposed | ✅ Protected |
| SQL dump (`mysqldump`, `pg_dump`) | ❌ Exposed | ✅ Protected |
| Application-level debug logs | ❌ Exposed | ✅ Protected (never decrypts to log) |
| Database admin insider threat | ❌ Exposed | ✅ Protected |

**Invariant**: If the database is fully dumped, `nid_number` will contain only Fernet tokens (`gAAAAA...`), never plaintext National IDs.

---

## 🏗️ Architecture Implemented

```
HTTP Request (plaintext NID)
        │
        ▼
 app/routers/user_router.py
   PUT /{user_id}/nid
        │
        ▼
 app/services/user_service.py
   UserService.update_nid()
        │
        ▼
 app/repositories/sqlalchemy_user_repository.py
   SqlAlchemyUserRepository.update_nid()
        │
        ▼
 SQLAlchemy ORM  ──►  TypeDecorator (EncryptedString)
                           │
                    FernetEngine.encrypt()
                           │
                           ▼
                    SQLite: "gAAAAA..." (ciphertext)

── On SELECT ──►  TypeDecorator.process_result_value()
                           │
                    FernetEngine.decrypt()
                           │
                           ▼
              UserEntity.nid_number = "19951234567890"
```

---

## 📦 Files Created / Modified

### New Files
| File | Purpose |
|---|---|
| `app/core/encryption.py` | `FernetEngine` + `EncryptedString` TypeDecorator |
| `tests/test_field_level_encryption.py` | 12-test comprehensive FLE verification suite |
| `alembic/versions/6c6aea5c9814_add_encrypted_nid_number_to_users.py` | Migration: `nid_number TEXT` column |

### Modified Files
| File | Change |
|---|---|
| `app/core/config.py` | Added `field_encryption_key` setting |
| `app/core/exceptions.py` | Added `EncryptionTamperingException`; fixed `UserNotFoundException.code = "USER_NOT_FOUND"` |
| `app/models/user.py` | Added `nid_number: Mapped[str | None] = mapped_column(EncryptedString)` |
| `app/repositories/user_repository.py` | Added `update_nid()` to protocol & `InMemoryUserRepository` |
| `app/repositories/sqlalchemy_user_repository.py` | Added `update_nid()` implementation |
| `app/schemas/user.py` | Added `UpdateUserNidRequest` schema, `nid_number` in `UserResponse` |
| `app/services/user_service.py` | Added `UserService.update_nid()` with cache write-through |
| `app/routers/user_router.py` | Added `PUT /{user_id}/nid` endpoint |
| `app/main.py` | Fixed critical import: `SqlAlchemyUserRepository` from correct module |

---

## 🔑 Key Implementation: FernetEngine

```python
# app/core/encryption.py
class FernetEngine:
    """AES-128-CBC + HMAC-SHA256 authenticated symmetric encryption engine."""
    
    def __init__(self, key: bytes | str) -> None:
        if isinstance(key, str):
            key = key.encode()
        self._fernet = Fernet(key)

    def encrypt(self, plaintext: str) -> str:
        return self._fernet.encrypt(plaintext.encode()).decode()

    def decrypt(self, ciphertext: str) -> str:
        try:
            return self._fernet.decrypt(ciphertext.encode()).decode()
        except (InvalidToken, Exception) as exc:
            raise EncryptionTamperingException(...) from exc
```

---

## 🔑 Key Implementation: EncryptedString TypeDecorator

```python
class EncryptedString(TypeDecorator):
    """Transparent SQLAlchemy TypeDecorator: plaintext in → ciphertext in DB → plaintext out."""
    impl = Text
    cache_ok = True

    def process_bind_param(self, value, dialect):
        """Encrypt before INSERT/UPDATE."""
        if value is None:
            return None
        return get_fernet_engine().encrypt(value)

    def process_result_value(self, value, dialect):
        """Decrypt after SELECT."""
        if value is None:
            return None
        return get_fernet_engine().decrypt(value)
```

---

## 🧪 Tests Implemented (12/12 ✅)

| # | Test | Category | Validates |
|---|---|---|---|
| 1 | `test_fernet_engine_encryption_roundtrip` | Unit | Encrypt → token starts `gAAAAA`, decrypts back |
| 2 | `test_fernet_engine_tampering_detection` | Unit | Mutated ciphertext raises `EncryptionTamperingException` |
| 3 | `test_fernet_engine_invalid_token` | Unit | Garbage string raises `EncryptionTamperingException` |
| 4 | `test_orm_transparent_round_trip_encryption` | ORM | Insert plaintext → SELECT returns decrypted plaintext |
| 5 | `test_raw_sql_storage_ciphertext_verification` | ORM+SQL | Raw SQL sees only `gAAAAA...`, never plaintext |
| 6 | `test_database_ciphertext_tampering_detection` | Security | Mutate DB row → ORM raises `EncryptionTamperingException` |
| 7 | `test_nullable_nid_number_handling` | ORM | `nid_number=None` persists and returns `None` cleanly |
| 8 | `test_sqlalchemy_user_repository_nid_operations` | Repo | Repository `create` and `update_nid` work correctly |
| 9 | `test_in_memory_user_repository_nid_operations` | Repo | In-memory repo `update_nid` works for unit testing |
| 10 | `test_user_service_update_nid_not_found` | Service | Service raises `UserNotFoundException` for missing user |
| 11 | `test_api_endpoint_update_user_nid` | Integration | E2E: Register → PUT NID → response has plaintext NID |
| 12 | `test_api_endpoint_update_user_nid_not_found` | Integration | 404 + `USER_NOT_FOUND` code for invalid user ID |

---

## 🐛 Bugs Found & Fixed

### Bug 1: Wrong `SqlAlchemyUserRepository` import in `main.py`
**Symptom**: App lifespan seeded Bloom Filter with `InMemoryUserRepository` instead of the real DB repo.  
**Root Cause**: `from app.repositories.user_repository import SqlAlchemyUserRepository` — wrong module.  
**Fix**: `from app.repositories.sqlalchemy_user_repository import SqlAlchemyUserRepository`

### Bug 2: Generic `UserNotFoundException.code = "ENTITY_NOT_FOUND"`
**Symptom**: `test_api_endpoint_update_user_nid_not_found` expected `USER_NOT_FOUND` but got `NOT_FOUND` (from Starlette's HTTP fallback, then `ENTITY_NOT_FOUND`).  
**Fix**: Changed default code to `"USER_NOT_FOUND"` for domain-specific error identification.

### Bug 3: Test paths used `/api/v1/users/` prefix that doesn't exist
**Symptom**: POST `/api/v1/users/` → 404 "Not Found" (Starlette route match failure).  
**Root Cause**: Router is mounted at `/users/`, not `/api/v1/users/`.  
**Fix**: Corrected test paths to `/users/` and `/users/{user_id}/nid`.

### Bug 4: Cross-session raw SQL verification in ASGI test
**Symptom**: `scalar_one()` raised `NoResultFound` — user_id existed in HTTP context, but external `async_session_factory()` couldn't see the committed row.  
**Fix**: Removed cross-session raw SQL from HTTP endpoint test (covered by dedicated `test_raw_sql_storage_ciphertext_verification`).

---

## 🧠 Concepts Mastered

1. **Authenticated Encryption**: Fernet = AES-128-CBC + HMAC-SHA256. Tampered ciphertext is cryptographically detectable.
2. **Defense in Depth**: FLE complements TDE — even if DB credentials leak, PII stays encrypted.
3. **TypeDecorator Pattern**: Transparent encryption at ORM boundary — business logic never sees ciphertext.
4. **Exception Code Specificity**: Domain errors should have semantic codes (`USER_NOT_FOUND`) not generic HTTP codes (`NOT_FOUND`).
5. **Test Isolation**: ASGI transport tests share a different transaction scope from direct `async_session_factory()` — raw SQL verification must be in the same session context.

---

## 📈 Test Count

| Before Day 49 | After Day 49 |
|---|---|
| ~470 tests | **482 tests** |
| - | +12 FLE tests |
