"""Centralized Pytest configuration and shared test fixtures."""

import sqlite3
from collections.abc import Generator
from datetime import UTC
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.core.dependencies import _user_repository, transaction_manager
from app.main import app
from app.repositories.product_repository import clear_product_locks
from app.repositories.user_repository import UserRepositoryProtocol
from app.routers.user_router import get_user_repository
from app.services.notification_service import clear_notification_service
from app.services.user_service import get_user_bloom_filter


def _clean_database() -> None:
    """Reset the database tables between tests for isolation."""
    settings = get_settings()
    if settings.database_url.startswith("sqlite"):
        db_path = settings.database_url.replace("sqlite+aiosqlite:///", "").replace("sqlite:///", "")
        if db_path and db_path != ":memory:":
            with sqlite3.connect(db_path) as conn:
                for table in [
                    "posts",
                    "users",
                    "products",
                    "catalog_items",
                    "audit_logs",
                    "searchable_products",
                    "audit_logs_y2025",
                    "audit_logs_y2026",
                    "audit_logs_default",
                ]:
                    try:
                        conn.execute(f"DELETE FROM {table}")  # noqa: S608
                    except sqlite3.OperationalError:
                        pass
                conn.commit()


@pytest.fixture(autouse=True)
def clean_repo() -> Generator[UserRepositoryProtocol]:
    """Autouse fixture ensuring clean, isolated repository, transaction, and background task state."""
    _user_repository.clear()
    clear_product_locks()
    _clean_database()
    transaction_manager.clear()
    clear_notification_service()
    get_user_bloom_filter().clear()
    app.dependency_overrides[get_user_repository] = lambda: _user_repository
    yield _user_repository
    app.dependency_overrides.pop(get_user_repository, None)
    _user_repository.clear()
    clear_product_locks()
    _clean_database()
    transaction_manager.clear()
    clear_notification_service()
    get_user_bloom_filter().clear()


@pytest.fixture
def clean_repository(clean_repo: UserRepositoryProtocol) -> UserRepositoryProtocol:
    """Backward compatibility fixture providing the clean repository instance."""
    return clean_repo


@pytest.fixture
def client() -> TestClient:
    """FastAPI TestClient fixture scoped per test function."""
    return TestClient(app)


@pytest.fixture
def auth_headers() -> dict[str, str]:
    """Headers containing valid standard user API key."""
    settings = get_settings()
    return {"X-API-Key": settings.user_api_key}


@pytest.fixture
def admin_auth_headers() -> dict[str, str]:
    """Headers containing valid administrator API key."""
    settings = get_settings()
    return {"X-API-Key": settings.admin_api_key}


@pytest.fixture
def standard_user(client: TestClient) -> dict[str, Any]:
    """Seed standard user associated with default user API key."""
    settings = get_settings()
    payload = {
        "email": "standard.user@example.com",
        "username": settings.default_username,
        "password": "Password123!",
        "password_confirm": "Password123!",
        "age": 25,
        "role": "user",
    }
    response = client.post("/users/", json=payload)
    assert response.status_code == 201
    user_data: dict[str, Any] = response.json()
    return user_data


@pytest.fixture
def admin_user(client: TestClient) -> dict[str, Any]:
    """Seed administrator user associated with admin API key."""
    settings = get_settings()
    payload = {
        "email": "admin.user@example.com",
        "username": settings.admin_username,
        "password": "Password123!",
        "password_confirm": "Password123!",
        "age": 30,
        "role": "admin",
    }
    response = client.post("/users/", json=payload)
    assert response.status_code == 201
    user_data: dict[str, Any] = response.json()
    return user_data


@pytest.fixture
def sample_user_payload() -> dict[str, Any]:
    """Fixture providing a standard valid user creation payload."""
    return {
        "email": "architect.test@example.com",
        "username": "architect_user",
        "password": "SecurePassword123!",
        "password_confirm": "SecurePassword123!",
        "age": 28,
        "role": "user",
        "full_name": "Lead Architect",
    }


@pytest.fixture
def enterprise_user_payload() -> dict[str, Any]:
    """Fixture providing a valid enterprise user creation payload."""
    return {
        "email": "enterprise.lead@example.com",
        "username": "enterprise_corp",
        "password": "EnterprisePassword123!",
        "password_confirm": "EnterprisePassword123!",
        "age": 35,
        "role": "enterprise",
        "company_name": "Global Tech Corp",
        "full_name": "Enterprise Lead",
    }


@pytest.fixture
def created_user(client: TestClient, sample_user_payload: dict[str, Any]) -> dict[str, Any]:
    """Fixture pre-populating a user via the HTTP transport boundary and returning response data."""
    response = client.post("/users/", json=sample_user_payload)
    assert response.status_code == 201, f"Failed to seed user fixture: {response.text}"
    user_data: dict[str, Any] = response.json()
    return user_data


@pytest.fixture
def enterprise_user(
    client: TestClient,
    enterprise_user_payload: dict[str, Any],
) -> dict[str, Any]:
    """Seed an enterprise user and return their entity data."""
    response = client.post("/users/", json=enterprise_user_payload)
    assert response.status_code == 201
    user_data: dict[str, Any] = response.json()
    return user_data


@pytest.fixture
def enterprise_auth_headers(enterprise_user: dict[str, Any]) -> dict[str, str]:
    """Provide authentication headers for the seeded enterprise user."""
    return {"X-API-Key": f"userkey_{enterprise_user['username']}"}


# ==============================================================================
# Day 28: Async Mock Fixtures & Test Doubles
# ==============================================================================


class MockNotificationService:
    """Test spy container for asynchronous background notification and audit tasks."""

    def __init__(self) -> None:
        self.send_welcome_notification = AsyncMock()
        self.record_audit_log = AsyncMock()


@pytest.fixture
def mock_user_repository() -> AsyncMock:
    """Reusable AsyncMock strictly adhering to UserRepositoryProtocol."""
    from datetime import datetime

    from app.repositories.user_repository import UserEntity

    mock = AsyncMock(spec=UserRepositoryProtocol)
    mock.get_by_id.return_value = None
    mock.get_by_email.return_value = None
    mock.get_by_username.return_value = None
    mock.create.return_value = UserEntity(
        id=1,
        email="mocked.user@example.com",
        username="mocked_user",
        password_hash="mocked_hash",
        is_active=True,
        created_at=datetime.now(UTC),
        age=25,
        role="user",
        full_name="Mocked User",
    )
    mock.update.return_value = UserEntity(
        id=1,
        email="mocked.user@example.com",
        username="mocked_user_updated",
        password_hash="mocked_hash",
        is_active=True,
        created_at=datetime.now(UTC),
        age=26,
        role="user",
        full_name="Mocked User Updated",
    )
    mock.delete.return_value = True
    mock.list_all.return_value = []
    return mock


@pytest.fixture
def mock_notification_service(monkeypatch: pytest.MonkeyPatch) -> Generator[MockNotificationService]:
    """Provide AsyncMock spies for background notification and audit logging tasks."""
    spy = MockNotificationService()
    monkeypatch.setattr("app.routers.user_router.send_welcome_notification", spy.send_welcome_notification)
    monkeypatch.setattr("app.routers.user_router.record_audit_log", spy.record_audit_log)
    yield spy


@pytest.fixture
def mock_db_session() -> AsyncMock:
    """AsyncMock simulating SQLAlchemy's AsyncSession with mockable execute, commit, and rollback."""
    from sqlalchemy.ext.asyncio import AsyncSession

    session = AsyncMock(spec=AsyncSession)
    session.execute = AsyncMock()
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    session.close = AsyncMock()
    return session


@pytest.fixture
def fake_redis() -> Generator[Any]:
    """Provide an isolated in-memory FakeRedis client with guaranteed teardown and dependency override."""
    import fakeredis.aioredis

    from app.core.redis import get_redis, set_redis_client_override

    client = fakeredis.aioredis.FakeRedis(decode_responses=True)
    set_redis_client_override(client)

    async def _override_get_redis() -> Any:
        yield client

    app.dependency_overrides[get_redis] = _override_get_redis
    yield client
    app.dependency_overrides.pop(get_redis, None)
    set_redis_client_override(None)


@pytest.fixture(autouse=True)
def configure_celery_eager_mode() -> Generator[None]:
    """Configure Celery to run in eager synchronous mode during tests with in-memory result backend."""
    import app.tasks.report_tasks as _report_tasks  # noqa: F401 (register Celery task definitions)
    import app.tasks.scheduled_tasks as _scheduled_tasks  # noqa: F401
    from app.core.celery_app import celery_app

    original_eager = celery_app.conf.task_always_eager
    original_propagates = celery_app.conf.task_eager_propagates
    original_store_eager = celery_app.conf.task_store_eager_result
    original_backend = celery_app.conf.result_backend

    celery_app.conf.update(
        task_always_eager=True,
        task_eager_propagates=True,
        task_store_eager_result=True,
        result_backend="cache+memory://",
    )
    celery_app._backend = celery_app._get_backend()

    # Synchronize task instances to store eager results into cache backend
    for task in celery_app.tasks.values():
        task.store_eager_result = True

    yield

    for task in celery_app.tasks.values():
        task.store_eager_result = False

    celery_app.conf.update(
        task_always_eager=original_eager,
        task_eager_propagates=original_propagates,
        task_store_eager_result=original_store_eager,
        result_backend=original_backend,
    )
    celery_app._backend = celery_app._get_backend()
