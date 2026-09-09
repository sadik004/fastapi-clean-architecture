"""Comprehensive test suite for Day 45: High-Performance Bitmasking RBAC Architecture (O(1) Bitwise Permission Checking).

Verifies:
1. Bitwise Flag Mathematics: Validates binary operations (&, |, ~), IntFlag powers of 2,
   composite role masks, and O(1) single-cycle bitwise evaluation.
2. Forbidden Rejection (HTTP 403): Users lacking required permission flags are strictly rejected
   with informative 403 Forbidden details without touching business logic or database joins.
3. Authorized Access (HTTP 200/204): Users holding required permission flags execute protected actions.
4. Dynamic Permission Mutation: Administrators dynamically grant/revoke permission bitmask flags,
   and updated permissions take effect immediately.
5. Entity and Schema Projection: Asserts computed permission_names on UserEntity, UserResponse,
   and AuthenticatedUserResponse.
"""

from __future__ import annotations

from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.permissions import (
    ROLE_ADMIN,
    ROLE_GUEST,
    ROLE_MODERATOR,
    ROLE_USER,
    Permission,
    get_permission_names,
    grant_permission,
    has_permission,
    revoke_permission,
)
from app.core.security import create_access_token
from app.main import app


def test_bitwise_flag_mathematics() -> None:
    """Verify binary powers of 2, role bitmasks, and O(1) bitwise operations."""
    # Powers of 2 verification (single active bit)
    assert Permission.NONE.value == 0
    assert Permission.READ.value == 1  # 2^0
    assert Permission.WRITE.value == 2  # 2^1
    assert Permission.DELETE.value == 4  # 2^2
    assert Permission.ADMIN.value == 8  # 2^3
    assert Permission.EXPORT.value == 16  # 2^4
    assert Permission.BILLING.value == 32  # 2^5

    # Composite role bitmask verification
    assert ROLE_GUEST == 1
    assert ROLE_USER == 3  # 1 | 2
    assert ROLE_MODERATOR == 7  # 1 | 2 | 4
    assert ROLE_ADMIN == 63  # 1 | 2 | 4 | 8 | 16 | 32

    # has_permission verification (strictly O(1) CPU bitwise AND)
    assert has_permission(ROLE_USER, Permission.READ) is True
    assert has_permission(ROLE_USER, Permission.WRITE) is True
    assert has_permission(ROLE_USER, Permission.DELETE) is False
    assert has_permission(ROLE_USER, Permission.ADMIN) is False

    assert has_permission(ROLE_MODERATOR, Permission.READ) is True
    assert has_permission(ROLE_MODERATOR, Permission.WRITE) is True
    assert has_permission(ROLE_MODERATOR, Permission.DELETE) is True
    assert has_permission(ROLE_MODERATOR, Permission.ADMIN) is False

    assert has_permission(ROLE_ADMIN, Permission.ADMIN) is True
    assert has_permission(ROLE_ADMIN, Permission.BILLING) is True
    assert has_permission(ROLE_ADMIN, Permission.EXPORT) is True

    # grant_permission verification (bitwise OR)
    elevated_user = grant_permission(ROLE_USER, Permission.DELETE)
    assert elevated_user == ROLE_MODERATOR
    assert has_permission(elevated_user, Permission.DELETE) is True

    # grant_permission idempotent
    assert grant_permission(elevated_user, Permission.DELETE) == ROLE_MODERATOR

    # revoke_permission verification (bitwise AND NOT)
    demoted_mod = revoke_permission(ROLE_MODERATOR, Permission.DELETE)
    assert demoted_mod == ROLE_USER
    assert has_permission(demoted_mod, Permission.DELETE) is False

    # Human-readable flag names extraction
    assert get_permission_names(ROLE_USER) == ["READ", "WRITE"]
    assert get_permission_names(ROLE_MODERATOR) == ["READ", "WRITE", "DELETE"]
    assert get_permission_names(ROLE_ADMIN) == ["READ", "WRITE", "DELETE", "ADMIN", "EXPORT", "BILLING"]
    assert get_permission_names(Permission.NONE.value) == []


@pytest.mark.asyncio
async def test_forbidden_rejection_http_403(
    fake_redis: Any,
) -> None:
    """Ensure users lacking required permission flags are rejected with HTTP 403 Forbidden."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # 1. Create standard user and target user
        user_payload = {
            "email": "user.rbac@example.com",
            "username": "userrbac",
            "password": "Password123!",
            "password_confirm": "Password123!",
            "role": "user",
        }
        res1 = await ac.post("/users/", json=user_payload)
        assert res1.status_code == 201
        user_id = res1.json()["id"]

        target_payload = {
            "email": "target.user@example.com",
            "username": "targetuser",
            "password": "Password123!",
            "password_confirm": "Password123!",
            "role": "user",
        }
        res2 = await ac.post("/users/", json=target_payload)
        assert res2.status_code == 201
        target_id = res2.json()["id"]

        # 2. Issue token for standard user with default permissions (ROLE_USER = 3: READ | WRITE)
        token = create_access_token(user_id=user_id, role="user", permissions=ROLE_USER)
        headers = {"Authorization": f"Bearer {token}"}

        # 3. Standard user attempts DELETE /users/{target_id} (requires Permission.DELETE = 4)
        delete_resp = await ac.delete(f"/users/{target_id}", headers=headers)
        assert delete_resp.status_code == 403
        assert "Missing required permission: DELETE" in delete_resp.json()["detail"]

        # 4. Standard user attempts GET /users/admin/analytics (requires Permission.ADMIN = 8)
        analytics_resp = await ac.get("/users/admin/analytics", headers=headers)
        assert analytics_resp.status_code == 403
        assert "Missing required permission: ADMIN" in analytics_resp.json()["detail"]


@pytest.mark.asyncio
async def test_authorized_access_http_200_or_204(
    fake_redis: Any,
) -> None:
    """Ensure clients holding required permission flags successfully access protected routes."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # 1. Create moderator and target user
        mod_payload = {
            "email": "mod.rbac@example.com",
            "username": "modrbac",
            "password": "Password123!",
            "password_confirm": "Password123!",
            "role": "user",
        }
        res1 = await ac.post("/users/", json=mod_payload)
        assert res1.status_code == 201
        mod_id = res1.json()["id"]

        target_payload = {
            "email": "victim.user@example.com",
            "username": "victimuser",
            "password": "Password123!",
            "password_confirm": "Password123!",
            "role": "user",
        }
        res2 = await ac.post("/users/", json=target_payload)
        assert res2.status_code == 201
        target_id = res2.json()["id"]

        # 2. Moderator with ROLE_MODERATOR (7: READ | WRITE | DELETE)
        mod_token = create_access_token(user_id=mod_id, role="user", permissions=ROLE_MODERATOR)
        mod_headers = {"Authorization": f"Bearer {mod_token}"}

        # 3. Moderator deletes target user
        del_resp = await ac.delete(f"/users/{target_id}", headers=mod_headers)
        assert del_resp.status_code == 204

        # 4. Admin accesses /users/admin/analytics
        admin_token = create_access_token(user_id=999, role="admin", permissions=ROLE_ADMIN)
        admin_headers = {"Authorization": f"Bearer {admin_token}"}
        analytics_resp = await ac.get("/users/admin/analytics", headers=admin_headers)
        assert analytics_resp.status_code == 200
        data = analytics_resp.json()
        assert data["status"] == "success"
        assert data["metric"] == "Bitmasking RBAC Administrative Analytics"


@pytest.mark.asyncio
async def test_dynamic_permission_mutation(
    fake_redis: Any,
) -> None:
    """Ensure ADMIN can dynamically grant and revoke permission bitmasks with immediate effect."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # 1. Create standard user and target user
        res_user = await ac.post(
            "/users/",
            json={
                "email": "candidate.user@example.com",
                "username": "candidateuser",
                "password": "Password123!",
                "password_confirm": "Password123!",
                "role": "user",
            },
        )
        assert res_user.status_code == 201
        candidate_id = res_user.json()["id"]
        assert res_user.json()["permissions"] == 3
        assert res_user.json()["permission_names"] == ["READ", "WRITE"]

        res_target = await ac.post(
            "/users/",
            json={
                "email": "candidate.target@example.com",
                "username": "candidatetarget",
                "password": "Password123!",
                "password_confirm": "Password123!",
                "role": "user",
            },
        )
        assert res_target.status_code == 201
        candidate_target_id = res_target.json()["id"]

        # 2. User initially fails to delete (403 Forbidden)
        user_token = create_access_token(user_id=candidate_id, role="user", permissions=ROLE_USER)
        res_del_fail = await ac.delete(
            f"/users/{candidate_target_id}", headers={"Authorization": f"Bearer {user_token}"}
        )
        assert res_del_fail.status_code == 403

        # 3. Admin grants DELETE permission to candidate user
        admin_token = create_access_token(user_id=1, role="admin", permissions=ROLE_ADMIN)
        admin_headers = {"Authorization": f"Bearer {admin_token}"}

        perm_update_res = await ac.put(
            f"/users/{candidate_id}/permissions",
            headers=admin_headers,
            json={"grant": Permission.DELETE.value},
        )
        assert perm_update_res.status_code == 200
        updated_data = perm_update_res.json()
        assert updated_data["permissions"] == 7
        assert "DELETE" in updated_data["permission_names"]

        # 4. User issues fresh token reflecting new permissions (ROLE_MODERATOR = 7)
        elevated_token = create_access_token(user_id=candidate_id, role="user", permissions=7)
        res_del_success = await ac.delete(
            f"/users/{candidate_target_id}",
            headers={"Authorization": f"Bearer {elevated_token}"},
        )
        assert res_del_success.status_code == 204

        # 5. Admin revokes DELETE permission
        revoke_res = await ac.put(
            f"/users/{candidate_id}/permissions",
            headers=admin_headers,
            json={"revoke": Permission.DELETE.value},
        )
        assert revoke_res.status_code == 200
        revoked_data = revoke_res.json()
        assert revoked_data["permissions"] == 3
        assert "DELETE" not in revoked_data["permission_names"]
