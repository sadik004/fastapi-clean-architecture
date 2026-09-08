"""Enterprise Async Testing Suite using pytest-asyncio, AsyncMock & Dependency Overrides.

Demonstrates:
1. Isolated Service Unit Testing with AsyncMock (zero database/network I/O).
2. Failure Injection & Resilience Verification (DB crash, SMTP timeout).
3. Async Test Double Spying (assert_awaited_once_with).
4. FastAPI Route Testing via app.dependency_overrides.
5. Sub-50ms Benchmark Verification for Mocked Async Operations.
"""

from datetime import datetime, timezone
import time
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from app.core.dsa.trie import PrefixTrie
from app.core.exceptions import UserAlreadyExistsException, UserNotFoundException
from app.main import app
from app.repositories.user_repository import UserEntity
from app.routers.user_router import get_user_repository
from app.schemas.user import UserCreate, UserRole
from app.services.user_service import UserService
from tests.conftest import MockNotificationService


# ==============================================================================
# 1. Isolated Service Unit Tests (Pure AsyncMock)
# ==============================================================================

@pytest.mark.asyncio
async def test_user_service_register_user_success(mock_user_repository: AsyncMock) -> None:
    """Verify UserService.register_user executes in memory with mocked async repository."""
    mock_entity = UserEntity(
        id=101,
        email="unit.test@example.com",
        username="unit_tester",
        password_hash="hashed_secret",
        is_active=True,
        created_at=datetime.now(timezone.utc),
        age=24,
        role="user",
        full_name="Unit Tester",
    )
    mock_user_repository.get_by_email.return_value = None
    mock_user_repository.get_by_username.return_value = None
    mock_user_repository.create.return_value = mock_entity

    service = UserService(repository=mock_user_repository, trie=PrefixTrie())
    payload = UserCreate(
        email="unit.test@example.com",
        username="unit_tester",
        password="ValidPassword123!",
        password_confirm="ValidPassword123!",
        age=24,
        role=UserRole.USER,
        full_name="Unit Tester",
    )

    created = await service.register_user(payload)

    assert created.id == 101
    assert created.email == "unit.test@example.com"
    assert created.username == "unit_tester"

    # Verify mock call interactions and invariants
    mock_user_repository.get_by_email.assert_awaited_once_with("unit.test@example.com")
    mock_user_repository.get_by_username.assert_awaited_once_with("unit_tester")
    mock_user_repository.create.assert_awaited_once()


@pytest.mark.asyncio
async def test_user_service_register_user_duplicate_email_rejection(
    mock_user_repository: AsyncMock,
) -> None:
    """Verify UserService rejects registration when mock repository detects duplicate email."""
    existing_user = UserEntity(
        id=99,
        email="existing@example.com",
        username="other_user",
        password_hash="hash",
        is_active=True,
        created_at=datetime.now(timezone.utc),
    )
    mock_user_repository.get_by_email.return_value = existing_user

    service = UserService(repository=mock_user_repository, trie=PrefixTrie())
    payload = UserCreate(
        email="existing@example.com",
        username="new_user",
        password="ValidPassword123!",
        password_confirm="ValidPassword123!",
        age=25,
    )

    with pytest.raises(UserAlreadyExistsException, match="already registered"):
        await service.register_user(payload)

    mock_user_repository.get_by_email.assert_awaited_once_with("existing@example.com")
    mock_user_repository.create.assert_not_awaited()


@pytest.mark.asyncio
async def test_user_service_get_by_id_and_delete(mock_user_repository: AsyncMock) -> None:
    """Verify get_user_by_id and delete_user interact accurately with mock repository."""
    user = UserEntity(
        id=77,
        email="delete.me@example.com",
        username="temp_user",
        password_hash="hash",
        is_active=True,
        created_at=datetime.now(timezone.utc),
    )
    mock_user_repository.get_by_id.return_value = user
    mock_user_repository.delete.return_value = True

    service = UserService(repository=mock_user_repository, trie=PrefixTrie())

    fetched = await service.get_user_by_id(77)
    assert fetched.id == 77
    mock_user_repository.get_by_id.assert_awaited_once_with(77)

    await service.delete_user(77)
    mock_user_repository.delete.assert_awaited_once_with(77)


@pytest.mark.asyncio
async def test_user_service_get_by_id_not_found(mock_user_repository: AsyncMock) -> None:
    """Verify UserService raises UserNotFoundException when mock returns None."""
    mock_user_repository.get_by_id.return_value = None
    service = UserService(repository=mock_user_repository, trie=PrefixTrie())

    with pytest.raises(UserNotFoundException):
        await service.get_user_by_id(999)

    mock_user_repository.get_by_id.assert_awaited_once_with(999)


# ==============================================================================
# 2. Failure Simulation & Resilience Verification
# ==============================================================================

@pytest.mark.asyncio
async def test_user_service_database_failure_injection(mock_user_repository: AsyncMock) -> None:
    """Verify UserService propagates database connection/disk failure without corruption."""
    mock_user_repository.get_by_email.return_value = None
    mock_user_repository.get_by_username.return_value = None
    mock_user_repository.create.side_effect = RuntimeError("Database disk full / connection timeout")

    service = UserService(repository=mock_user_repository, trie=PrefixTrie())
    payload = UserCreate(
        email="fail.inject@example.com",
        username="fail_user",
        password="ValidPassword123!",
        password_confirm="ValidPassword123!",
        age=30,
    )

    with pytest.raises(RuntimeError, match="Database disk full / connection timeout"):
        await service.register_user(payload)

    mock_user_repository.create.assert_awaited_once()


def test_route_resilience_background_task_smtp_timeout_isolation(
    mock_notification_service: MockNotificationService,
) -> None:
    """Verify API endpoint returns HTTP 201 even when background notification times out."""
    mock_notification_service.send_welcome_notification.side_effect = TimeoutError("SMTP timeout")

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
    data = response.json()
    assert data["email"] == "smtp.timeout@example.com"
    assert "X-Process-Time-Ms" in response.headers
    assert "X-Request-ID" in response.headers


# ==============================================================================
# 3. Spy & Interaction Verification
# ==============================================================================

def test_notification_service_spies_verified_on_user_creation(
    client: TestClient,
    mock_notification_service: MockNotificationService,
) -> None:
    """Verify background tasks are dispatched with exact arguments using AsyncMock spies."""
    payload = {
        "email": "spy.verify@example.com",
        "username": "spy_user",
        "password": "Password123!",
        "password_confirm": "Password123!",
        "age": 31,
        "role": "user",
    }

    response = client.post("/users/", json=payload)
    assert response.status_code == 201
    created_id = response.json()["id"]

    mock_notification_service.send_welcome_notification.assert_awaited_once_with(
        "spy.verify@example.com",
        "spy_user",
    )
    mock_notification_service.record_audit_log.assert_awaited_once()
    assert mock_notification_service.record_audit_log.await_args is not None
    call_args = mock_notification_service.record_audit_log.await_args[0]
    assert call_args[0] == "create_user"
    assert call_args[1] == created_id
    assert isinstance(call_args[2], datetime)


# ==============================================================================
# 4. Route Testing via app.dependency_overrides
# ==============================================================================

def test_route_dependency_override_create_user(
    client: TestClient,
    mock_user_repository: AsyncMock,
) -> None:
    """Verify route executes against mock repository via app.dependency_overrides."""
    mock_entity = UserEntity(
        id=555,
        email="override.route@example.com",
        username="override_user",
        password_hash="mock_hash",
        is_active=True,
        created_at=datetime.now(timezone.utc),
        age=27,
        role="user",
        full_name="Override Route User",
    )
    mock_user_repository.get_by_email.return_value = None
    mock_user_repository.get_by_username.return_value = None
    mock_user_repository.create.return_value = mock_entity

    app.dependency_overrides[get_user_repository] = lambda: mock_user_repository
    try:
        payload = {
            "email": "override.route@example.com",
            "username": "override_user",
            "password": "Password123!",
            "password_confirm": "Password123!",
            "age": 27,
            "role": "user",
            "full_name": "Override Route User",
        }
        response = client.post("/users/", json=payload)
        assert response.status_code == 201
        data = response.json()
        assert data["id"] == 555
        assert data["email"] == "override.route@example.com"
        mock_user_repository.create.assert_awaited_once()
    finally:
        app.dependency_overrides.pop(get_user_repository, None)


def test_route_dependency_override_get_user_by_id(
    client: TestClient,
    mock_user_repository: AsyncMock,
) -> None:
    """Verify GET /users/{id} retrieves user from overridden mock repository."""
    mock_entity = UserEntity(
        id=888,
        email="retrieved@example.com",
        username="retrieved_user",
        password_hash="mock_hash",
        is_active=True,
        created_at=datetime.now(timezone.utc),
        age=33,
        role="user",
    )
    mock_user_repository.get_by_id.return_value = mock_entity

    app.dependency_overrides[get_user_repository] = lambda: mock_user_repository
    try:
        response = client.get("/users/888")
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == 888
        assert data["username"] == "retrieved_user"
        mock_user_repository.get_by_id.assert_awaited_once_with(888)
    finally:
        app.dependency_overrides.pop(get_user_repository, None)


# ==============================================================================
# 5. Sub-50ms Benchmark Verification for Mocked Async Operations
# ==============================================================================

@pytest.mark.asyncio
async def test_mock_batch_execution_sub_50ms_benchmark(mock_user_repository: AsyncMock) -> None:
    """Verify 10 mock-isolated async service operations execute in under 50ms."""
    mock_user_repository.get_by_id.return_value = UserEntity(
        id=1,
        email="bench@example.com",
        username="bench_user",
        password_hash="hash",
        is_active=True,
        created_at=datetime.now(timezone.utc),
    )
    service = UserService(repository=mock_user_repository, trie=PrefixTrie())

    start = time.perf_counter()
    for _ in range(10):
        user = await service.get_user_by_id(1)
        assert user.id == 1
    duration_ms = (time.perf_counter() - start) * 1000.0

    assert mock_user_repository.get_by_id.await_count == 10
    # Strict latency gate: 10 mock operations must execute in < 50ms
    assert duration_ms < 50.0, f"Benchmark exceeded budget: {duration_ms:.2f}ms >= 50ms"
