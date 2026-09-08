# RCA: Day 18 Repository Swapping, Standalone Invocation & Test Isolation Regression

- **Trigger**: 
  1. `TypeError: get_user_repository() missing 1 required positional argument: 'session'` occurred in `tests/test_auth_dependencies.py` and `tests/test_path_query_validation.py`.
  2. `AssertionError: assert 401 == 204` occurred in `test_end_to_end_api_crud_with_database` due to missing database admin seeding.
  3. Pre-existing unit tests mutating in-memory user objects failed when endpoints persisted to SQLite without synchronous test database resets.

---

## 1. Incident 1: Breaking Standalone Callers via Mandatory `session` Dependency

### Faulty Code / Pattern
```python
# app/core/dependencies.py
def get_user_repository(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> UserRepositoryProtocol:
    """Production repository dependency provider."""
    return SqlAlchemyUserRepository(session=session)
```

```python
# tests/test_path_query_validation.py
repo = get_user_repository()  # CRASH! TypeError: missing 1 required positional argument: 'session'
```

### Root Cause
While FastAPI's dependency injection container automatically resolves `Depends(get_db_session)` and supplies the `session` keyword argument during HTTP requests, direct callers in unit test fixtures, scripts, and legacy tests invoked `get_user_repository()` with zero arguments. Making `session` a mandatory positional parameter immediately broke all non-FastAPI invocation contexts.

### Resolution
Make the `session` parameter optional with a default value of `None`. If `session` is provided by FastAPI dependency resolution, instantiate and return `SqlAlchemyUserRepository(session=session)`. If called directly without arguments, cleanly fall back to `_user_repository` (`InMemoryUserRepository`):

```python
# app/core/dependencies.py
def get_user_repository(
    session: Annotated[Optional[AsyncSession], Depends(get_db_session)] = None,
) -> UserRepositoryProtocol:
    """Dependency provider for UserRepositoryProtocol.

    In FastAPI request lifecycles, injects an active AsyncSession from get_db_session
    and yields a production SqlAlchemyUserRepository.
    When invoked without a session argument (e.g., isolated unit test assertions),
    falls back to the in-memory repository.
    """
    if session is not None:
        return SqlAlchemyUserRepository(session=session)
    return _user_repository
```

---

## 2. Incident 2: In-Memory Test State Mutation vs Persistent Relational Database

### Faulty Code / Pattern
```python
# tests/test_auth_dependencies.py
def test_get_users_me_inactive_user_returns_403(client, standard_user, auth_headers):
    user_id = int(standard_user["id"])
    repo = get_user_repository()
    user = asyncio.run(repo.get_by_id(user_id))
    assert user is not None
    user.is_active = False  # Mutating in-memory entity reference
```

### Root Cause
1. In Days 1–17, `_user_repository` was an in-memory dictionary. Mutating `user.is_active = False` directly altered the Python object stored inside `_store[user_id]`.
2. When endpoints were wired to `SqlAlchemyUserRepository`, `client.post("/users/", ...)` persisted records to the real SQLite database (`app.db`), returning detached `UserEntity` dataclasses.
3. Modifying an attribute on a detached `UserEntity` has no effect on database records.
4. Furthermore, SQLite records persisted across test functions because `clean_repo` only cleared in-memory dictionaries, leading to `409 Conflict` duplicate key errors on consecutive test runs.

### Resolution
Establish a dual-mode testing architecture in `tests/conftest.py`:
1. Synchronously purge database records between every test using a dedicated `_clean_database()` helper.
2. Wire `app.dependency_overrides[get_user_repository] = lambda: _user_repository` in `clean_repo` so that fast unit tests execute against the isolated in-memory repository mock.
3. In `tests/test_sqlalchemy_user_repository.py`, explicitly pop the dependency override (`app.dependency_overrides.pop(get_user_repository, None)`) to test real database persistence end-to-end, and seed necessary admin credentials directly into the database.

```python
# tests/conftest.py
def _clean_database() -> None:
    """Reset the database users table between tests for isolation."""
    settings = get_settings()
    if settings.database_url.startswith("sqlite"):
        db_path = settings.database_url.replace("sqlite+aiosqlite:///", "").replace("sqlite:///", "")
        if db_path and db_path != ":memory:":
            try:
                with sqlite3.connect(db_path) as conn:
                    conn.execute("DELETE FROM users")
                    conn.commit()
            except sqlite3.OperationalError:
                pass

@pytest.fixture(autouse=True)
def clean_repo() -> Generator[UserRepositoryProtocol, None, None]:
    """Autouse fixture ensuring clean, isolated repository, transaction, and background task state."""
    _user_repository.clear()
    _clean_database()
    transaction_manager.clear()
    clear_notification_service()
    app.dependency_overrides[get_user_repository] = lambda: _user_repository
    yield _user_repository
    app.dependency_overrides.pop(get_user_repository, None)
    _user_repository.clear()
    _clean_database()
    transaction_manager.clear()
    clear_notification_service()
```

---

## Permanent Prevention Rules Added to SKILL.md
1. **Dual-Mode Repository Dependency Provider**: Structure repository dependencies with optional session parameters (`session: Annotated[Optional[AsyncSession], Depends(get_db_session)] = None`) so endpoints receive production database repositories while standalone test calls and mock suites continue to work seamlessly without signature errors.
2. **Synchronous Test Database Sanitization**: In projects supporting relational persistence, always register a test cleanup fixture that purges database tables (`DELETE FROM table`) alongside in-memory stores before and after each test function.
3. **Explicit Dependency Override Management**: When writing end-to-end tests for a newly implemented concrete repository, explicitly remove or scope `app.dependency_overrides` to ensure the HTTP pipeline genuinely exercises the database persistence layer.
