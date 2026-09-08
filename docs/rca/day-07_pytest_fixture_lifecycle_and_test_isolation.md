# RCA: Day 07 Pytest Fixture Lifecycle, Inter-Test State Pollution, and Duplication Traps

- **Trigger**: Test order dependencies and code duplication discovered during Pytest architecture refactoring.

---

## 1. Incident 1: In-Memory Inter-Test State Pollution

### Faulty Code / Pattern
```python
# tests/conftest.py (or individual test files without autouse teardown)
@pytest.fixture
def clean_repo():
    repo = _user_repository
    repo.clear()
    return repo
```

### Root Cause
Without a generator-based `yield` teardown and `autouse=True`, tests that did not explicitly declare the `clean_repo` argument inherited previously populated users in `InMemoryUserRepository._store` and `_email_index`. Subsequent test suites asserting on empty pagination offsets or attempting to create a user with a previously used email (e.g. `test@example.com`) failed with unexpected `409 Conflict` errors depending on test execution order.

### Resolution
Converted the fixture in `tests/conftest.py` to an `autouse=True` generator that resets repository storage and secondary indexes both **before** test entry and **after** test exit:
```python
# tests/conftest.py
@pytest.fixture(autouse=True)
def clean_repo() -> Generator[None, None, None]:
    """Ensure in-memory repository is 100% purged before and after every test."""
    repo = _user_repository
    repo.clear()
    yield
    repo.clear()
```

---

## 2. Incident 2: Copy-Paste Fixture Duplication Across Test Modules

### Faulty Code / Pattern
```python
# tests/test_user.py
client = TestClient(app)

# tests/test_user_crud.py
client = TestClient(app)

# tests/test_path_query_validation.py
client = TestClient(app)
```

### Root Cause
Individual test files repeatedly instantiated `TestClient(app)` at module scope or within local fixtures. This broke the single-responsibility principle of test infrastructure, risked conflicting transport configurations, and bypassed centralized fixture lifecycles.

### Resolution
Centralized the single source of truth in `tests/conftest.py` with strict generator typing, enabling all downstream test modules to inject `client: TestClient` effortlessly:
```python
# tests/conftest.py
@pytest.fixture
def client() -> Generator[TestClient, None, None]:
    """Provide a scoped FastAPI TestClient instance."""
    with TestClient(app) as test_client:
        yield test_client
```

---

## Permanent Prevention Rules
1. **Always Use Centralized `conftest.py`**: Never define standalone `TestClient` instances or ad-hoc repository clear routines inside individual test files.
2. **Enforce `autouse=True` with `yield` for Stateful Storage**: Any in-memory storage, mock cache, or test database fixture must guarantee clean state before and after every test execution.
3. **Annotate Generators Strictly for Mypy**: Use `Generator[YieldType, SendType, ReturnType]` (e.g., `Generator[None, None, None]`) on all pytest generator fixtures to remain 100% compliant with `mypy --strict`.
