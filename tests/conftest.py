"""Centralized Pytest configuration and shared test fixtures."""

from typing import Any, Generator
import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.main import app
from app.repositories.user_repository import UserRepositoryProtocol
from app.routers.user_router import get_user_repository


@pytest.fixture(autouse=True)
def clean_repo() -> Generator[UserRepositoryProtocol, None, None]:
    """Autouse fixture ensuring clean, isolated repository state before and after every test."""
    repo = get_user_repository()
    repo.clear()
    yield repo
    repo.clear()


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

