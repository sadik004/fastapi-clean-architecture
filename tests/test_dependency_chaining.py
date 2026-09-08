"""Comprehensive test suite for Day 09: Dependency Chaining, Sub-Dependencies & Parameterized Class Guards."""

from collections.abc import Generator
from typing import Any

import pytest
from fastapi import APIRouter, Depends, status
from fastapi.testclient import TestClient

from app.core.dependencies import (
    RoleChecker,
    get_current_user,
    require_user_ownership,
)
from app.main import app
from app.repositories.user_repository import UserEntity
from app.schemas.user import UserRole

# ============================================================================
# Auxiliary Router for Sub-Dependency DAG and Role Combination Checks
# ============================================================================

dag_router = APIRouter(prefix="/test-dag", tags=["Test DAG"])


@dag_router.get("/vip-lounge")
def vip_lounge(
    user: UserEntity = Depends(RoleChecker([UserRole.ADMIN, UserRole.ENTERPRISE])),
) -> dict[str, str]:
    """Accessible by Admin or Enterprise users only."""
    return {"message": f"Welcome {user.username} to VIP lounge"}


@dag_router.get("/multi-subdep/{user_id}")
def multi_subdep_endpoint(
    owner: UserEntity = Depends(require_user_ownership),
    direct_user: UserEntity = Depends(get_current_user),
) -> dict[str, Any]:
    """Endpoint depending on both require_user_ownership (which depends on get_current_user)

    and get_current_user directly to test request-scoped memoization (use_cache=True).
    """
    assert owner.id == direct_user.id
    return {
        "status": "cached",
        "owner_id": owner.id,
        "direct_user_id": direct_user.id,
    }


@pytest.fixture(scope="module", autouse=True)
def register_dag_router() -> Generator[None]:
    """Register auxiliary router on application for module test lifecycle."""
    app.include_router(dag_router)
    yield


# ============================================================================
# 1. Parameterized Callable Class Guards (RoleChecker) Tests
# ============================================================================


class TestRoleCheckerGuard:
    """Verify parameterized RoleChecker enforcing O(1) role membership."""

    def test_admin_metrics_success_for_admin(
        self,
        client: TestClient,
        admin_user: dict[str, Any],
        admin_auth_headers: dict[str, str],
    ) -> None:
        """Verify Admin successfully accesses /users/admin/metrics."""
        response = client.get("/users/admin/metrics", headers=admin_auth_headers)
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["status"] == "operational"
        assert "total_users" in data
        assert "admin_count" in data
        assert data["admin_count"] >= 1

    def test_admin_metrics_denied_for_standard_user(
        self,
        client: TestClient,
        standard_user: dict[str, Any],
        auth_headers: dict[str, str],
    ) -> None:
        """Verify regular user receives HTTP 403 on admin metrics endpoint."""
        response = client.get("/users/admin/metrics", headers=auth_headers)
        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert response.json()["detail"] == "Insufficient role permissions"

    def test_vip_lounge_role_combinations(
        self,
        client: TestClient,
        standard_user: dict[str, Any],
        auth_headers: dict[str, str],
        enterprise_user: dict[str, Any],
        enterprise_auth_headers: dict[str, str],
        admin_user: dict[str, Any],
        admin_auth_headers: dict[str, str],
    ) -> None:
        """Verify RoleChecker([ADMIN, ENTERPRISE]) allows enterprise & admin but rejects standard user."""
        # Standard user -> 403
        std_resp = client.get("/test-dag/vip-lounge", headers=auth_headers)
        assert std_resp.status_code == status.HTTP_403_FORBIDDEN
        assert std_resp.json()["detail"] == "Insufficient role permissions"

        # Enterprise user -> 200
        ent_resp = client.get("/test-dag/vip-lounge", headers=enterprise_auth_headers)
        assert ent_resp.status_code == status.HTTP_200_OK
        assert "Welcome" in ent_resp.json()["message"]

        # Admin user -> 200
        adm_resp = client.get("/test-dag/vip-lounge", headers=admin_auth_headers)
        assert adm_resp.status_code == status.HTTP_200_OK
        assert "Welcome" in adm_resp.json()["message"]


# ============================================================================
# 2. Hierarchical Dependency Chaining & IDOR Protection Tests
# ============================================================================


class TestIDOROwnershipGuard:
    """Verify require_user_ownership chained dependency preventing IDOR attacks."""

    def test_user_can_update_own_profile_via_put(
        self,
        client: TestClient,
    ) -> None:
        """Verify User A can successfully update User A's profile via PUT."""
        create_resp = client.post(
            "/users/",
            json={
                "email": "owner_a@example.com",
                "username": "user_owner_a",
                "password": "Password123!",
                "password_confirm": "Password123!",
                "age": 22,
                "role": "user",
            },
        )
        assert create_resp.status_code == status.HTTP_201_CREATED
        user_a = create_resp.json()
        headers_a = {"X-API-Key": "userkey_user_owner_a"}

        put_resp = client.put(
            f"/users/{user_a['id']}",
            json={"full_name": "Owner A Renamed", "bio": "Personal Bio"},
            headers=headers_a,
        )
        assert put_resp.status_code == status.HTTP_200_OK
        assert put_resp.json()["full_name"] == "Owner A Renamed"

    def test_user_can_update_own_profile_via_patch(
        self,
        client: TestClient,
    ) -> None:
        """Verify User A can successfully update User A's profile via PATCH."""
        create_resp = client.post(
            "/users/",
            json={
                "email": "owner_patch@example.com",
                "username": "user_owner_patch",
                "password": "Password123!",
                "password_confirm": "Password123!",
                "age": 24,
                "role": "user",
            },
        )
        assert create_resp.status_code == status.HTTP_201_CREATED
        user = create_resp.json()
        headers = {"X-API-Key": "userkey_user_owner_patch"}

        patch_resp = client.patch(
            f"/users/{user['id']}",
            json={"bio": "Updated Bio via Patch"},
            headers=headers,
        )
        assert patch_resp.status_code == status.HTTP_200_OK
        assert patch_resp.json()["bio"] == "Updated Bio via Patch"

    def test_user_cannot_update_other_user_profile_idor_forbidden(
        self,
        client: TestClient,
    ) -> None:
        """Verify User A attempting to update User B's profile is blocked with HTTP 403 (IDOR Prevention)."""
        # Create User A
        user_a = client.post(
            "/users/",
            json={
                "email": "victim@example.com",
                "username": "victim_user",
                "password": "Password123!",
                "password_confirm": "Password123!",
                "age": 25,
            },
        ).json()

        # Create User B (Attacker)
        client.post(
            "/users/",
            json={
                "email": "attacker@example.com",
                "username": "attacker_user",
                "password": "Password123!",
                "password_confirm": "Password123!",
                "age": 26,
            },
        )
        attacker_headers = {"X-API-Key": "userkey_attacker_user"}

        # Attacker attempts PUT on victim's ID
        put_resp = client.put(
            f"/users/{user_a['id']}",
            json={"full_name": "Hacked Name"},
            headers=attacker_headers,
        )
        assert put_resp.status_code == status.HTTP_403_FORBIDDEN
        assert put_resp.json()["detail"] == "Access forbidden: you cannot modify another user's profile"

        # Attacker attempts PATCH on victim's ID
        patch_resp = client.patch(
            f"/users/{user_a['id']}",
            json={"bio": "Hacked Bio"},
            headers=attacker_headers,
        )
        assert patch_resp.status_code == status.HTTP_403_FORBIDDEN
        assert patch_resp.json()["detail"] == "Access forbidden: you cannot modify another user's profile"

    def test_admin_can_update_any_user_profile(
        self,
        client: TestClient,
        admin_user: dict[str, Any],
        admin_auth_headers: dict[str, str],
        created_user: dict[str, Any],
    ) -> None:
        """Verify Administrator can update any user's profile bypassing ownership."""
        target_id = created_user["id"]
        response = client.patch(
            f"/users/{target_id}",
            json={"full_name": "Admin Modified Name"},
            headers=admin_auth_headers,
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.json()["full_name"] == "Admin Modified Name"


# ============================================================================
# 3. Sub-Dependency Request-Scoped Caching Tests (use_cache=True)
# ============================================================================


class TestSubDependencyRequestScopedCaching:
    """Verify FastAPI evaluates sub-dependencies exactly once per request DAG."""

    def test_get_current_user_evaluated_once_per_request(
        self,
        client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Verify that multiple dependencies referencing get_current_user evaluate it only once."""
        create_resp = client.post(
            "/users/",
            json={
                "email": "dag_cache@example.com",
                "username": "dag_user",
                "password": "Password123!",
                "password_confirm": "Password123!",
                "age": 30,
            },
        )
        assert create_resp.status_code == status.HTTP_201_CREATED
        user = create_resp.json()
        headers = {"X-API-Key": "userkey_dag_user"}

        call_count = 0
        from app.services.user_service import UserService

        original_get_by_username = UserService.get_user_by_username

        async def spy_get_by_username(svc_self: UserService, username: str) -> UserEntity:
            nonlocal call_count
            call_count += 1
            return await original_get_by_username(svc_self, username=username)

        monkeypatch.setattr(UserService, "get_user_by_username", spy_get_by_username)

        resp = client.get(f"/test-dag/multi-subdep/{user['id']}", headers=headers)
        assert resp.status_code == status.HTTP_200_OK
        data = resp.json()
        assert data["status"] == "cached"
        assert data["owner_id"] == user["id"]
        assert data["direct_user_id"] == user["id"]

        # Because use_cache=True by default in FastAPI Depends(),
        # the underlying domain user resolution executes strictly ONCE in the request DAG.
        assert call_count == 1
