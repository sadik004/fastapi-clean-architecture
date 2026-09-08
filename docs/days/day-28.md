# Day 28: Advanced Async Testing with pytest-asyncio, AsyncMock & Dependency Overrides

**Date**: 2026-09-09  
**Role**: Junior Apprentice Backend Engineer  
**Lead Architect & Mentor**: User

---

## 1. Concepts Covered Today
- **Async Test Double Architecture (`unittest.mock.AsyncMock`)**:
  - Implemented centralized test fixtures adhering strictly to `UserRepositoryProtocol` using `AsyncMock(spec=UserRepositoryProtocol)`.
  - Configured mock return values (`create`, `get_by_id`, `get_by_email`, `get_by_username`, `delete`, `list_all`) executing in 100% in-memory time without touching any disk or database.
  - Eliminated the risk of `RuntimeWarning: coroutine was never awaited` or `TypeError` caused by attaching synchronous `MagicMock` to `async def` methods.
- **Resilience & Chaos Failure Injection**:
  - Tested `UserService` under database failure conditions by setting `mock_user_repository.create.side_effect = RuntimeError("Database disk full / connection timeout")`.
  - Verified that domain services cleanly propagate and isolate system errors without internal state corruption.
  - Validated external notification gateway resilience by setting `mock_notification_service.send_welcome_notification.side_effect = TimeoutError("SMTP timeout")`, confirming that background task failures do not alter or break the HTTP 201 Created client response.
- **Async Spying & Interaction Verification**:
  - Utilized `assert_awaited_once_with()` to verify that background task arguments (`email`, `username`, `action`, `user_id`, `timestamp`) match exact expected values without invoking real notification transports.
- **FastAPI Route Testing via `app.dependency_overrides`**:
  - Replaced repository provider `get_user_repository` with a factory returning `mock_user_repository`.
  - Executed `POST /users/` and `GET /users/{id}` in sub-millisecond time with zero database I/O.
  - Enforced strict `try...finally:` and fixture teardowns with `app.dependency_overrides.pop(get_user_repository, None)` to guarantee zero inter-test state pollution.
- **Sub-50ms Execution Benchmark**:
  - Verified a batch of 10 mocked async operations executes in $< 50\text{ms}$ ($< 15\text{ms}$ actual), proving zero-I/O overhead.

---

## 2. Key Code Artifacts
- `tests/conftest.py`: Added `MockNotificationService`, `mock_user_repository`, `mock_notification_service`, and `mock_db_session` fixtures.
- `tests/test_async_mocking.py`: Comprehensive test suite containing 10 tests verifying unit isolation, failure injection, interaction spying, route dependency overrides, and latency benchmarks.

---

## 3. Verification & Quality Gates
- **Pytest**: 350 tests passed across the complete repository (100% pass rate in 19.91s).
- **Mypy**: `mypy --strict app tests alembic` passed cleanly with 0 errors across 42 source files.
- **Ruff**: `ruff check app tests alembic` passed cleanly.
- **Benchmark**: 10 mock operations executed in $< 15\text{ms}$, satisfying the $< 50\text{ms}$ latency budget.

---

## 4. Root Cause Analysis (RCA)
- See `docs/rca/day-28_async_testing_mock_lifecycles_and_dependency_overrides.md` for detailed analysis regarding Starlette TestClient background task error propagation, synchronous MagicMock hazards, and dependency override state hygiene.
