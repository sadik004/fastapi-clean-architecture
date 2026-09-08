# RCA: Day 28 - Async Testing TestClient Background Task Isolation, Synchronous MagicMock Hazards, and Dependency Override Hygiene

- **Date**: 2026-09-09
- **Role**: Junior Apprentice Backend Engineer
- **Lead Architect & Mentor**: User
- **Topic**: pytest-asyncio, AsyncMock Invariants, Starlette TestClient Exception Re-raising, and Dependency Override State Isolation

---

## 1. Trigger & Incident Scenario

During Day 28 test suite execution for failure injection in `tests/test_async_mocking.py`:
1. `test_route_resilience_background_task_smtp_timeout_isolation` failed with:
   ```text
   C:\Users\User\anaconda3\Lib\unittest\mock.py:2321: in _execute_mock_call
       raise effect
   E   TimeoutError: SMTP timeout
   FAILED tests/test_async_mocking.py::test_route_resilience_background_task_smtp_timeout_isolation
   ```
2. Traceback revealed that Starlette's `TestClient` executes background tasks in-thread via `await self.background()`. Because `raise_server_exceptions=True` (the default in TestClient), the exception raised inside the mock background task was re-raised into the test process, causing the test assertion to fail before inspecting the response status code.

---

## 2. Faulty Code / Anti-Patterns

### Anti-Pattern A: Default `TestClient` Re-raising Background Task Errors in Resilience Tests
```python
# FAULTY: TestClient(app, raise_server_exceptions=True) re-raises background task failures
def test_route_resilience_background_task_smtp_timeout_isolation(
    client: TestClient,  # Default TestClient has raise_server_exceptions=True
    mock_notification_service: MockNotificationService,
) -> None:
    mock_notification_service.send_welcome_notification.side_effect = TimeoutError("SMTP timeout")
    response = client.post("/users/", json=payload)  # Crashes test before inspecting response!
    assert response.status_code == 201
```

### Anti-Pattern B: Using Synchronous `MagicMock` on Asynchronous Coroutines
```python
# FAULTY: Using MagicMock instead of AsyncMock
mock_repo = MagicMock(spec=UserRepositoryProtocol)
# Invoking `await mock_repo.create(...)` raises:
# TypeError: object MagicMock can't be used in 'await' expression
```

### Anti-Pattern C: Leaking `app.dependency_overrides` Without Teardown
```python
# FAULTY: Mutating global dependency overrides without guaranteed cleanup
def test_something():
    app.dependency_overrides[get_user_repository] = lambda: mock_repo
    client.get("/users/1")
    # Missing teardown! mock_repo now leaks into ALL subsequent test files!
```

---

## 3. Root Cause Analysis

1. **Starlette TestClient vs Production ASGI Transport Lifecycle**:
   - In production (e.g. Uvicorn), when an ASGI endpoint yields a response, the HTTP response headers and status code (`201 Created`) are finalized and transmitted across the network to the client. Background tasks are scheduled afterward. If a background task encounters an unhandled exception, Uvicorn logs the error to `stderr`, but the client connection has already closed successfully.
   - Starlette's `TestClient` simulates the entire lifecycle synchronously within `portal.call()`. When `raise_server_exceptions=True`, any exception occurring during background task execution is intercepted and raised into the caller's thread.
   - To verify gateway failure isolation (confirming that an external SMTP timeout in background execution does not fail the already-committed HTTP 201 response), the test client must be instantiated with `raise_server_exceptions=False`.
2. **`MagicMock` vs `AsyncMock` Invariant**:
   - Python's `async/await` syntax requires the target callable to return an awaitable object implementing `__await__()`. A standard `MagicMock` returns another `MagicMock` instance upon call, which is not an awaitable coroutine. `AsyncMock` is purpose-built to return an async coroutine object that tracks `await_count`, `await_args`, and `assert_awaited_once_with()`.
3. **Global Dependency Registry Hygiene**:
   - `app.dependency_overrides` is a mutable dictionary stored directly on the singleton `FastAPI` instance. If an override is injected and an assertion fails (or teardown is omitted), the override persists across the entire Pytest session, causing cascading, non-deterministic test failures in unrelated test files.

---

## 4. Correct Architectural Solution

### 1. Transport-Accurate Resilience Verification (`tests/test_async_mocking.py`)
```python
def test_route_resilience_background_task_smtp_timeout_isolation(
    mock_notification_service: MockNotificationService,
) -> None:
    """Verify API endpoint returns HTTP 201 even when background notification times out."""
    mock_notification_service.send_welcome_notification.side_effect = TimeoutError("SMTP timeout")

    # Mirror production ASGI behavior where background errors do not break the HTTP response
    client = TestClient(app, raise_server_exceptions=False)
    payload = {
        "email": "smtp.timeout@example.com",
        "username": "smtp_user",
        "password": "Password123!",
        "password_confirm": "Password123!",
        "age": 29,
        "role": "user",
    }

    response = client.post("/users/", json=payload)
    assert response.status_code == 201
    assert response.json()["email"] == "smtp.timeout@example.com"
```

### 2. Spec-Bound `AsyncMock` Fixtures (`tests/conftest.py`)
```python
@pytest.fixture
def mock_user_repository() -> AsyncMock:
    """Reusable AsyncMock strictly adhering to UserRepositoryProtocol."""
    mock = AsyncMock(spec=UserRepositoryProtocol)
    mock.get_by_id.return_value = None
    mock.get_by_email.return_value = None
    mock.get_by_username.return_value = None
    mock.create.return_value = UserEntity(...)
    mock.delete.return_value = True
    mock.list_all.return_value = []
    return mock
```

### 3. Guaranteed `try...finally:` Teardown for Dependency Overrides
```python
app.dependency_overrides[get_user_repository] = lambda: mock_user_repository
try:
    response = client.post("/users/", json=payload)
    assert response.status_code == 201
finally:
    app.dependency_overrides.pop(get_user_repository, None)
```

---

## 5. Prevention & Verification Rules

1. **Rule: Always Use `AsyncMock` for Async Contracts**:
   - Never use `MagicMock` for `async def` methods or protocols. Always use `AsyncMock(spec=ProtocolClass)` to guarantee protocol conformance and proper coroutine awaitability.
2. **Rule: Use `raise_server_exceptions=False` When Testing Background Task Fault Tolerance**:
   - When verifying that a background task exception does not invalidate an HTTP response, configure `TestClient(app, raise_server_exceptions=False)` to prevent Starlette from re-raising post-response exceptions into the test thread.
3. **Rule: Always Clear or Pop `app.dependency_overrides` in Teardown**:
   - Every modification to `app.dependency_overrides` must be enclosed in a `try...finally:` block or a generator fixture with a `yield` / teardown step to eliminate test state pollution.
