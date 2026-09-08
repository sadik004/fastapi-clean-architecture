"""Day 08: Dependency Injection Architecture & Declarative Auth Guards Test Suite."""

from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.core.config import Settings, get_settings
from app.routers.user_router import get_user_repository

# ============================================================================
# 1. Centralized Configuration & Caching Tests
# ============================================================================


class TestConfigurationInjection:
    """Verify application configuration loading, immutability, and LRU cache singleton behavior."""

    def test_get_settings_cached_singleton(self) -> None:
        """Verify @lru_cache returns the exact same object reference across multiple calls."""
        settings_1 = get_settings()
        settings_2 = get_settings()
        assert settings_1 is settings_2, "get_settings() must return a cached singleton instance."

    def test_settings_immutability(self) -> None:
        """Verify settings model is frozen and rejects runtime attribute mutations."""
        settings = get_settings()
        attr_name = "debug"
        with pytest.raises(ValidationError):
            setattr(settings, attr_name, True)

    def test_settings_defaults(self) -> None:
        """Verify expected default application configurations."""
        settings = Settings()
        assert settings.app_name == "FastAPI Clean Architecture"
        assert settings.environment == "development"
        assert settings.api_v1_prefix == "/api/v1"
        assert settings.debug is False
        assert len(settings.admin_api_key) >= 16
        assert len(settings.user_api_key) >= 16


# ============================================================================
# 2. Declarative Authentication Guard Tests (GET /users/me)
# ============================================================================


class TestAuthenticationGuards:
    """Verify declarative authentication behavior, header validation, and timing attack resistance."""

    def test_get_users_me_missing_header_returns_401(self, client: TestClient) -> None:
        """Verify accessing /users/me without X-API-Key header returns HTTP 401."""
        response = client.get("/users/me")
        assert response.status_code == 401
        assert response.json()["detail"] == "Missing API Key header"

    def test_get_users_me_invalid_token_returns_401(self, client: TestClient) -> None:
        """Verify invalid API Key values return HTTP 401."""
        response = client.get("/users/me", headers={"X-API-Key": "invalid_api_key_value"})
        assert response.status_code == 401
        assert response.json()["detail"] == "Invalid authentication credentials"

    def test_get_users_me_unseeded_user_returns_401(
        self,
        client: TestClient,
        auth_headers: dict[str, str],
    ) -> None:
        """Verify valid key format returns 401 if associated user does not exist in repository."""
        response = client.get("/users/me", headers=auth_headers)
        assert response.status_code == 401
        assert response.json()["detail"] == "Invalid authentication credentials"

    def test_get_users_me_valid_user_returns_200(
        self,
        client: TestClient,
        standard_user: dict[str, Any],
        auth_headers: dict[str, str],
    ) -> None:
        """Verify valid user key returns 200 with current user profile envelope."""
        response = client.get("/users/me", headers=auth_headers)
        assert response.status_code == 200
        assert response.headers["content-type"] == "application/json"

        data = response.json()
        assert data["id"] == standard_user["id"]
        assert data["username"] == standard_user["username"]
        assert data["email"] == standard_user["email"]
        assert data["is_active"] is True

        # Strict sensitive and transient field omission
        assert "password" not in data
        assert "password_hash" not in data
        assert "password_confirm" not in data

    def test_get_users_me_inactive_user_returns_403(
        self,
        client: TestClient,
        standard_user: dict[str, Any],
        auth_headers: dict[str, str],
    ) -> None:
        """Verify de-activated accounts are denied access with HTTP 403 Forbidden."""
        import asyncio

        user_id = int(standard_user["id"])
        repo = get_user_repository()
        user = asyncio.run(repo.get_by_id(user_id))
        assert user is not None
        user.is_active = False

        response = client.get("/users/me", headers=auth_headers)
        assert response.status_code == 403
        assert response.json()["detail"] == "Inactive user account"

    def test_dynamic_userkey_authentication(self, client: TestClient) -> None:
        """Verify dynamic token pattern userkey_<username> securely authenticates registered users."""
        payload = {
            "email": "dynamic.auth@example.com",
            "username": "dynamic_hero",
            "password": "Password123!",
            "password_confirm": "Password123!",
            "role": "user",
        }
        create_resp = client.post("/users/", json=payload)
        assert create_resp.status_code == 201

        dynamic_headers = {"X-API-Key": "userkey_dynamic_hero"}
        me_resp = client.get("/users/me", headers=dynamic_headers)
        assert me_resp.status_code == 200
        assert me_resp.json()["username"] == "dynamic_hero"


# ============================================================================
# 3. Declarative Administrator Authorization Guard Tests
# ============================================================================


class TestAuthorizationAdminGuards:
    """Verify role-based access control and destructive operation protection."""

    def test_delete_user_missing_header_returns_401(
        self,
        client: TestClient,
        created_user: dict[str, Any],
    ) -> None:
        """Verify DELETE without auth header is blocked at boundary with HTTP 401."""
        user_id = created_user["id"]
        response = client.delete(f"/users/{user_id}")
        assert response.status_code == 401
        assert response.json()["detail"] == "Missing API Key header"

    def test_delete_user_standard_user_returns_403(
        self,
        client: TestClient,
        standard_user: dict[str, Any],
        auth_headers: dict[str, str],
        created_user: dict[str, Any],
    ) -> None:
        """Verify regular authenticated users are denied administrative delete privileges with HTTP 403."""
        target_id = created_user["id"]
        response = client.delete(f"/users/{target_id}", headers=auth_headers)
        assert response.status_code == 403
        assert response.json()["detail"] == "Administrative privileges required"

    def test_delete_user_admin_user_returns_204(
        self,
        client: TestClient,
        admin_user: dict[str, Any],
        admin_auth_headers: dict[str, str],
        created_user: dict[str, Any],
    ) -> None:
        """Verify administrators successfully execute destructive actions returning HTTP 204."""
        target_id = created_user["id"]
        response = client.delete(f"/users/{target_id}", headers=admin_auth_headers)
        assert response.status_code == 204
        assert response.text == ""

        # Verify entity was deleted
        assert client.get(f"/users/{target_id}").status_code == 404
