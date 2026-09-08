# RCA: Day 10 Unseeded Auth Fixture Dependency and Generator Yield Teardown

- **Trigger**: Test failure `assert 401 == 404` in `test_user_not_found_triggers_rollback` during Day 10 lifecycle verification.

---

## 1. Incident 1: Unseeded Authentication Entity in Privileged Teardown Test

### Faulty Code / Pattern
```python
# tests/test_dependency_yield_lifecycle.py
def test_user_not_found_triggers_rollback(
    client: TestClient,
    admin_auth_headers: dict[str, str],  # Missing admin_user fixture!
) -> None:
    response = client.delete("/users/999999", headers=admin_auth_headers)
    assert response.status_code == status.HTTP_404_NOT_FOUND  # FAILED: Got 401
```

### Root Cause
`admin_auth_headers` supplies the HTTP header `{"X-API-Key": settings.admin_api_key}`. However, `get_current_user` validates that the associated administrative user entity actually exists in `InMemoryUserRepository` via `service.get_user_by_username(settings.admin_username)`. Because the `admin_user` fixture was omitted from the test function signature, the admin entity was never seeded into storage. Consequently, `get_current_user` rejected the request at the authentication gate with HTTP 401 Unauthorized before the route logic and service `UserNotFoundException` (HTTP 404) could be reached.

### Resolution
Always declare dependent state fixtures (e.g., `admin_user: dict[str, Any]`) alongside credential header fixtures (`admin_auth_headers: dict[str, str]`) in test signatures to guarantee entity persistence before route invocation:
```python
# tests/test_dependency_yield_lifecycle.py
def test_user_not_found_triggers_rollback(
    client: TestClient,
    admin_user: dict[str, Any],  # Pre-seeds the admin user entity
    admin_auth_headers: dict[str, str],
) -> None:
    response = client.delete("/users/999999", headers=admin_auth_headers)
    assert response.status_code == status.HTTP_404_NOT_FOUND
```

---

## 2. Incident 2: Dangling Resource Traps in Dependencies Without `finally:`

### Faulty Code / Pattern
```python
# app/core/dependencies.py
def get_transaction_context():
    tx = transaction_manager.begin()
    yield tx
    tx.commit()
    # Missing finally: block! If route raises HTTPException, tx never closes!
```

### Root Cause
In generator dependencies, when route handlers or sub-dependencies raise an exception (such as `HTTPException(404)`), FastAPI terminates the generator via `gen.throw(exc)`. If post-yield cleanup code is placed outside a `finally:` block, code execution immediately aborts, leaving transactions open, locks held, and sessions leaked in memory.

### Resolution
Enforce two-phase execution with `try...except...finally:` wrapping the `yield` statement. Commit on normal exit, rollback on exception, and unconditionally release resources in `finally:`:
```python
# app/core/dependencies.py
def get_transaction_context() -> Generator[ScopedTransactionContext, None, None]:
    tx = transaction_manager.begin()
    try:
        yield tx
        if tx.status == TransactionStatus.ACTIVE:
            tx.commit()
    except Exception:
        tx.rollback()
        raise
    finally:
        transaction_manager.close(tx)
```

---

## Permanent Prevention Rules
1. **Always Wrap Yield in `try...finally:`**: In any generator dependency managing resources (transactions, connections, file descriptors, audit timers), cleanup MUST reside in a `finally:` block.
2. **Coupled Auth Fixture Declaration**: In tests requiring privileged authorization, always declare the entity seeding fixture (`admin_user`) whenever requesting the header fixture (`admin_auth_headers`).
3. **Automate Rollback on Exceptions**: When an exception is thrown through `yield`, catch it, trigger rollback, and re-raise (`raise`) to maintain exception transparency for FastAPI exception handlers.
