"""Day 07: Pytest Architecture, TestClient Mastery & Parametrized Verification Suite.

This module organizes testing into structured test classes with extensive parametrization,
verifying HTTP transport boundaries, schema contracts, header assertions, and strict isolation.
"""

from typing import Any
import pytest
from fastapi.testclient import TestClient


# ============================================================================
# 1. Registration Test Suite (Class-based & Parametrized)
# ============================================================================


class TestUserRegistration:
    """Test suite covering user registration endpoint, DTO validation, and security boundaries."""

    @pytest.mark.parametrize(
        ("role", "company_name", "age", "username"),
        [
            ("user", None, 18, "boundary_user_18"),
            ("user", None, 120, "boundary_user_120"),
            ("admin", None, 30, "system_admin_30"),
            ("enterprise", "Acme Corporation", 40, "corp_partner_40"),
        ],
    )
    def test_valid_user_creation_matrix(
        self,
        client: TestClient,
        role: str,
        company_name: str | None,
        age: int,
        username: str,
    ) -> None:
        """Verify valid user combinations across roles, boundary ages, and company requirements."""
        payload: dict[str, Any] = {
            "email": f"{username}@testdomain.com",
            "username": username,
            "password": "SecurePassword123!",
            "password_confirm": "SecurePassword123!",
            "age": age,
            "role": role,
        }
        if company_name is not None:
            payload["company_name"] = company_name

        response = client.post("/users/", json=payload)
        assert response.status_code == 201
        assert response.headers["content-type"] == "application/json"

        data = response.json()
        assert data["username"] == username
        assert data["role"] == role
        assert data["age"] == age
        assert data["is_active"] is True
        assert "id" in data
        assert "created_at" in data

        # Strict security contract: sensitive and transient fields must NEVER be returned
        assert "password" not in data
        assert "password_hash" not in data
        assert "password_confirm" not in data

    @pytest.mark.parametrize(
        ("invalid_username", "reason"),
        [
            ("ab", "too_short_less_than_3_chars"),
            ("a" * 51, "too_long_greater_than_50_chars"),
            ("bad__handle", "consecutive_underscores_prohibited"),
            ("admin", "reserved_keyword_admin"),
            ("root", "reserved_keyword_root"),
            ("system", "reserved_keyword_system"),
            ("superuser", "reserved_keyword_superuser"),
            ("user-dash", "hyphen_prohibited"),
            ("user.dot", "dot_prohibited"),
            ("user space", "spaces_prohibited"),
            ("user@special", "symbols_prohibited"),
            ("user!exclamation", "exclamation_prohibited"),
        ],
    )
    def test_invalid_username_matrix_returns_422(
        self,
        client: TestClient,
        sample_user_payload: dict[str, Any],
        invalid_username: str,
        reason: str,
    ) -> None:
        """Verify invalid username variations are rejected with HTTP 422."""
        payload = {**sample_user_payload, "username": invalid_username}
        response = client.post("/users/", json=payload)
        assert response.status_code == 422, f"Expected 422 for reason: {reason}"

    def test_username_auto_lowercasing_and_trimming(
        self,
        client: TestClient,
        sample_user_payload: dict[str, Any],
    ) -> None:
        """Verify username is auto-lowercased and whitespace trimmed by Pydantic perimeter sanitizer."""
        payload = {**sample_user_payload, "username": "  MixedCapsAndSpaces  "}
        response = client.post("/users/", json=payload)
        assert response.status_code == 201
        assert response.json()["username"] == "mixedcapsandspaces"

    @pytest.mark.parametrize(
        "invalid_email",
        [
            "plainaddress",
            "@missingusername.com",
            "missingdomain@.com",
            "missingat sign.com",
            "two@@domain.com",
        ],
    )
    def test_invalid_email_matrix_returns_422(
        self,
        client: TestClient,
        sample_user_payload: dict[str, Any],
        invalid_email: str,
    ) -> None:
        """Verify malformed email addresses are rejected with HTTP 422."""
        payload = {**sample_user_payload, "email": invalid_email}
        response = client.post("/users/", json=payload)
        assert response.status_code == 422

    @pytest.mark.parametrize("invalid_age", [17, 0, -5, 121, 500, "underage"])
    def test_invalid_age_bounds_returns_422(
        self,
        client: TestClient,
        sample_user_payload: dict[str, Any],
        invalid_age: object,
    ) -> None:
        """Verify ages outside [18, 120] range are rejected with HTTP 422."""
        payload = {**sample_user_payload, "age": invalid_age}
        response = client.post("/users/", json=payload)
        assert response.status_code == 422

    def test_password_mismatch_returns_422(
        self,
        client: TestClient,
        sample_user_payload: dict[str, Any],
    ) -> None:
        """Verify mismatched password and password_confirm are rejected with HTTP 422."""
        payload = {
            **sample_user_payload,
            "password": "Password123!",
            "password_confirm": "DifferentPassword123!",
        }
        response = client.post("/users/", json=payload)
        assert response.status_code == 422
        assert "password" in response.text.lower()

    def test_password_containing_username_returns_422(
        self,
        client: TestClient,
        sample_user_payload: dict[str, Any],
    ) -> None:
        """Verify credential integrity rejection when password contains username."""
        username = sample_user_payload["username"]
        payload = {
            **sample_user_payload,
            "password": f"Secret{username}123!",
            "password_confirm": f"Secret{username}123!",
        }
        response = client.post("/users/", json=payload)
        assert response.status_code == 422

    def test_enterprise_role_missing_company_name_returns_422(
        self,
        client: TestClient,
        sample_user_payload: dict[str, Any],
    ) -> None:
        """Verify Enterprise role strictly requires company_name."""
        payload = {
            **sample_user_payload,
            "role": "enterprise",
            "company_name": None,
        }
        response = client.post("/users/", json=payload)
        assert response.status_code == 422


# ============================================================================
# 2. Retrieval Test Suite (Class-based & Parametrized)
# ============================================================================


class TestUserRetrieval:
    """Test suite covering ID and slug-based user retrieval endpoints."""

    def test_get_existing_user_by_id(
        self,
        client: TestClient,
        created_user: dict[str, Any],
    ) -> None:
        """Verify retrieving an existing user by ID returns 200 and matches response schema."""
        user_id = created_user["id"]
        response = client.get(f"/users/{user_id}")
        assert response.status_code == 200
        assert response.headers["content-type"] == "application/json"
        data = response.json()
        assert data["id"] == user_id
        assert data["username"] == created_user["username"]
        assert data["email"] == created_user["email"]

    def test_get_non_existent_user_returns_404(self, client: TestClient) -> None:
        """Verify valid integer ID for a non-existent user returns HTTP 404."""
        response = client.get("/users/99999")
        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()

    @pytest.mark.parametrize(
        "invalid_id",
        [0, -1, -50, "abc", "1.5", 2_147_483_648],
    )
    def test_invalid_path_id_bounds_returns_422(
        self,
        client: TestClient,
        invalid_id: object,
    ) -> None:
        """Verify path parameter validation rejects non-positive or out-of-bound IDs with HTTP 422."""
        response = client.get(f"/users/{invalid_id}")
        assert response.status_code == 422

    def test_get_user_by_username_slug_success(
        self,
        client: TestClient,
        created_user: dict[str, Any],
    ) -> None:
        """Verify retrieving a user by normalized username slug returns 200 OK."""
        username = created_user["username"]
        response = client.get(f"/users/by-username/{username}")
        assert response.status_code == 200
        data = response.json()
        assert data["username"] == username
        assert data["id"] == created_user["id"]

    @pytest.mark.parametrize(
        "invalid_slug",
        ["ab", "Bad_Caps", "user-dash", "user@special", "a" * 51],
    )
    def test_invalid_username_slug_returns_422(
        self,
        client: TestClient,
        invalid_slug: str,
    ) -> None:
        """Verify invalid username slug patterns are rejected with HTTP 422."""
        response = client.get(f"/users/by-username/{invalid_slug}")
        assert response.status_code == 422


# ============================================================================
# 3. Query Filtering & Pagination Test Suite
# ============================================================================


class TestUserQueryFiltering:
    """Test suite covering query parameter constraints, filtering logic, and pagination."""

    @pytest.mark.parametrize("invalid_limit", [0, -1, 101, 500, "abc"])
    def test_invalid_limit_bounds_returns_422(
        self,
        client: TestClient,
        invalid_limit: object,
    ) -> None:
        """Verify limit parameter enforces 1 <= limit <= 100 with HTTP 422."""
        response = client.get(f"/users/?limit={invalid_limit}")
        assert response.status_code == 422

    @pytest.mark.parametrize("invalid_offset", [-1, -50, "abc"])
    def test_invalid_offset_bounds_returns_422(
        self,
        client: TestClient,
        invalid_offset: object,
    ) -> None:
        """Verify offset parameter enforces offset >= 0 with HTTP 422."""
        response = client.get(f"/users/?offset={invalid_offset}")
        assert response.status_code == 422

    @pytest.mark.parametrize(
        "invalid_search",
        ["a", "a" * 51, "search<tag>", "search;drop", "search@symbol"],
    )
    def test_invalid_search_query_returns_422(
        self,
        client: TestClient,
        invalid_search: str,
    ) -> None:
        """Verify search query parameter enforces min_length=2, max_length=50, and alphanumeric pattern."""
        response = client.get(f"/users/?search={invalid_search}")
        assert response.status_code == 422

    def test_filter_by_role_matrix(
        self,
        client: TestClient,
        sample_user_payload: dict[str, Any],
        enterprise_user_payload: dict[str, Any],
    ) -> None:
        """Verify filtering by role returns strictly matching subsets."""
        # Seed standard user and enterprise user
        client.post("/users/", json=sample_user_payload)
        client.post("/users/", json=enterprise_user_payload)

        # Query enterprise only
        enterprise_resp = client.get("/users/?role=enterprise")
        assert enterprise_resp.status_code == 200
        enterprise_list = enterprise_resp.json()
        assert len(enterprise_list) == 1
        assert enterprise_list[0]["role"] == "enterprise"

        # Query standard user only
        user_resp = client.get("/users/?role=user")
        assert user_resp.status_code == 200
        user_list = user_resp.json()
        assert len(user_list) == 1
        assert user_list[0]["role"] == "user"


# ============================================================================
# 4. User Lifecycle Test Suite
# ============================================================================


class TestUserLifecycle:
    """Test suite verifying end-to-end user lifecycle and state cleanup guarantees."""

    def test_full_lifecycle_and_index_purging(
        self,
        client: TestClient,
        admin_user: dict[str, Any],
        admin_auth_headers: dict[str, str],
        sample_user_payload: dict[str, Any],
    ) -> None:
        """Verify CREATE -> READ -> UPDATE -> PATCH -> DELETE -> 404 -> RE-REGISTER flow."""
        email = sample_user_payload["email"]
        username = sample_user_payload["username"]

        # 1. CREATE
        create_resp = client.post("/users/", json=sample_user_payload)
        assert create_resp.status_code == 201
        user_id = create_resp.json()["id"]

        # 2. READ
        read_resp = client.get(f"/users/{user_id}")
        assert read_resp.status_code == 200
        assert read_resp.json()["email"] == email

        # 3. UPDATE via PUT (update full_name)
        put_resp = client.put(f"/users/{user_id}", json={"full_name": "Updated Architect Name"})
        assert put_resp.status_code == 200
        assert put_resp.json()["full_name"] == "Updated Architect Name"

        # 4. PARTIAL UPDATE via PATCH (update bio)
        patch_resp = client.patch(f"/users/{user_id}", json={"bio": "Architectural leader."})
        assert patch_resp.status_code == 200
        assert patch_resp.json()["bio"] == "Architectural leader."

        # 5. DELETE (204 No Content) - Authorized via admin_auth_headers
        del_resp = client.delete(f"/users/{user_id}", headers=admin_auth_headers)
        assert del_resp.status_code == 204

        # 6. VERIFY 404 AFTER DELETION
        get_deleted_resp = client.get(f"/users/{user_id}")
        assert get_deleted_resp.status_code == 404

        # 7. RE-REGISTER WITH SAME IDENTIFIERS (Proves secondary indexes were properly purged)
        recreate_resp = client.post("/users/", json=sample_user_payload)
        assert recreate_resp.status_code == 201
        assert recreate_resp.json()["username"] == username
        assert recreate_resp.json()["email"] == email
