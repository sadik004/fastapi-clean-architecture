"""Comprehensive test suite for FastAPI Path and Query parameter validation."""

from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.routers.user_router import get_user_repository

# ==========================================
# 1. Path Parameter Validation: user_id
# ==========================================


@pytest.mark.parametrize(
    "invalid_id",
    [0, -1, -999, "abc", "1.5", 2_147_483_648],
)
def test_path_user_id_bounds_get(client: TestClient, invalid_id: object) -> None:
    """Verify GET /users/{user_id} rejects non-positive or out-of-bound IDs with HTTP 422."""
    response = client.get(f"/users/{invalid_id}")
    assert response.status_code == 422


@pytest.mark.parametrize(
    "invalid_id",
    [0, -1, "abc", 2_147_483_648],
)
def test_path_user_id_bounds_all_endpoints(
    client: TestClient,
    admin_user: dict[str, Any],
    admin_auth_headers: dict[str, str],
    invalid_id: object,
) -> None:
    """Verify PUT, PATCH, and DELETE endpoints enforce the same Path(ge=1, le=2_147_483_647) constraints."""
    put_resp = client.put(
        f"/users/{invalid_id}",
        json={"full_name": "New Name"},
        headers=admin_auth_headers,
    )
    assert put_resp.status_code == 422

    patch_resp = client.patch(
        f"/users/{invalid_id}",
        json={"full_name": "New Name"},
        headers=admin_auth_headers,
    )
    assert patch_resp.status_code == 422

    delete_resp = client.delete(f"/users/{invalid_id}", headers=admin_auth_headers)
    assert delete_resp.status_code == 422


def test_path_user_id_valid_integer_not_found(client: TestClient) -> None:
    """Verify a valid positive integer ID passes validation and returns 404 when non-existent."""
    response = client.get("/users/999")
    assert response.status_code == 404
    assert "was not found" in response.json()["detail"].lower()


# ==========================================
# 2. Path Parameter Validation: username slug
# ==========================================


@pytest.mark.parametrize(
    "invalid_username",
    [
        "ab",  # < 3 chars
        "a" * 51,  # > 50 chars
        "Bad-Dash",  # hyphen not allowed
        "UpperCaseUser",  # uppercase not allowed by regex ^[a-z0-9_]+$
        "bad user",  # spaces not allowed
        "bad@domain",  # special symbols not allowed
        "user.dot",  # dots not allowed
    ],
)
def test_path_username_regex_validation(client: TestClient, invalid_username: str) -> None:
    """Verify GET /users/by-username/{username} rejects invalid username patterns with 422."""
    response = client.get(f"/users/by-username/{invalid_username}")
    assert response.status_code == 422


def test_path_username_lookup_success_and_not_found(client: TestClient) -> None:
    """Verify valid username formats pass path validation, returning 200 when found or 404 when missing."""
    # 404 when not found
    not_found_resp = client.get("/users/by-username/nonexistent_user")
    assert not_found_resp.status_code == 404
    assert "not found" in not_found_resp.json()["detail"].lower()

    # Create user and verify 200 lookup
    payload = {
        "email": "username.test@example.com",
        "username": "lookup_user_42",
        "password": "Password123!",
        "password_confirm": "Password123!",
        "role": "user",
    }
    create_resp = client.post("/users/", json=payload)
    assert create_resp.status_code == 201

    get_resp = client.get("/users/by-username/lookup_user_42")
    assert get_resp.status_code == 200
    data = get_resp.json()
    assert data["username"] == "lookup_user_42"
    assert data["email"] == "username.test@example.com"


# ==========================================
# 3. Query Parameter Validation: limit & offset
# ==========================================


@pytest.mark.parametrize("invalid_limit", [0, -1, -50, 101, 500, "abc"])
def test_query_limit_bounds(client: TestClient, invalid_limit: object) -> None:
    """Verify GET /users/?limit=... enforces 1 <= limit <= 100 with HTTP 422."""
    response = client.get(f"/users/?limit={invalid_limit}")
    assert response.status_code == 422


@pytest.mark.parametrize("invalid_offset", [-1, -50, "abc"])
def test_query_offset_bounds(client: TestClient, invalid_offset: object) -> None:
    """Verify GET /users/?offset=... enforces offset >= 0 with HTTP 422."""
    response = client.get(f"/users/?offset={invalid_offset}")
    assert response.status_code == 422


# ==========================================
# 4. Query Parameter Validation: role, search, is_active
# ==========================================


def test_query_role_enum_validation(client: TestClient) -> None:
    """Verify invalid roles are rejected with 422 while valid UserRoles pass."""
    invalid_resp = client.get("/users/?role=superman")
    assert invalid_resp.status_code == 422

    for valid_role in ["user", "admin", "enterprise"]:
        valid_resp = client.get(f"/users/?role={valid_role}")
        assert valid_resp.status_code == 200


@pytest.mark.parametrize(
    "invalid_search",
    [
        "a",  # min_length=2 violation
        "a" * 51,  # max_length=50 violation
        "bad<script>",  # regex violation
        "user@email.com",  # regex violation
        "drop table;",  # regex violation
    ],
)
def test_query_search_regex_and_length_validation(client: TestClient, invalid_search: str) -> None:
    """Verify search query parameter enforces min_length=2, max_length=50, and alphanumeric pattern."""
    response = client.get(f"/users/?search={invalid_search}")
    assert response.status_code == 422


def test_query_is_active_boolean_validation(client: TestClient) -> None:
    """Verify is_active parses standard boolean query representations or rejects invalid strings."""
    assert client.get("/users/?is_active=true").status_code == 200
    assert client.get("/users/?is_active=false").status_code == 200
    assert client.get("/users/?is_active=1").status_code == 200
    assert client.get("/users/?is_active=0").status_code == 200
    assert client.get("/users/?is_active=maybe").status_code == 422


# ==========================================
# 5. End-to-End Filtering and Pagination
# ==========================================


def _seed_sample_users(client: TestClient) -> list[dict[str, object]]:
    """Helper to seed diverse user records for filtering tests."""
    users_data = [
        {
            "email": "alice@alpha.com",
            "username": "alice_alpha",
            "password": "Password123!",
            "password_confirm": "Password123!",
            "role": "user",
            "full_name": "Alice Wonder",
        },
        {
            "email": "bob@admin.org",
            "username": "bob_admin",
            "password": "Password123!",
            "password_confirm": "Password123!",
            "role": "admin",
            "full_name": "Bob Builder",
        },
        {
            "email": "charlie@enterprise.com",
            "username": "charlie_corp",
            "password": "Password123!",
            "password_confirm": "Password123!",
            "role": "enterprise",
            "company_name": "Enterprise Inc",
            "full_name": "Charlie Chaplin",
        },
        {
            "email": "dana@enterprise.com",
            "username": "dana_corp",
            "password": "Password123!",
            "password_confirm": "Password123!",
            "role": "enterprise",
            "company_name": "Enterprise Inc",
            "full_name": "Dana Scully",
        },
    ]
    created = []
    for u in users_data:
        resp = client.post("/users/", json=u)
        assert resp.status_code == 201
        created.append(resp.json())
    return created


def test_filter_by_role(client: TestClient) -> None:
    """Verify filtering by role returns only matching users."""
    _seed_sample_users(client)

    resp = client.get("/users/?role=enterprise")
    assert resp.status_code == 200
    items = resp.json()
    assert len(items) == 2
    assert all(u["role"] == "enterprise" for u in items)


def test_filter_by_search_query(client: TestClient) -> None:
    """Verify search matches username or full_name case-insensitively."""
    _seed_sample_users(client)

    # Search by part of full_name
    resp_full_name = client.get("/users/?search=wonder")
    assert resp_full_name.status_code == 200
    items = resp_full_name.json()
    assert len(items) == 1
    assert items[0]["username"] == "alice_alpha"

    # Search by part of username
    resp_username = client.get("/users/?search=corp")
    assert resp_username.status_code == 200
    items = resp_username.json()
    assert len(items) == 2
    usernames = {u["username"] for u in items}
    assert usernames == {"charlie_corp", "dana_corp"}


def test_filter_by_is_active(client: TestClient) -> None:
    """Verify filtering by is_active returns matching active/inactive users."""
    seeded = _seed_sample_users(client)

    # Deactivate one user directly in repository
    import asyncio

    first_id_val = seeded[0]["id"]
    assert isinstance(first_id_val, int)
    first_id = first_id_val
    repo = get_user_repository()
    user = asyncio.run(repo.get_by_id(first_id))
    assert user is not None
    user.is_active = False

    # Active users
    active_resp = client.get("/users/?is_active=true")
    assert active_resp.status_code == 200
    active_items = active_resp.json()
    assert len(active_items) == 3
    assert all(u["is_active"] is True for u in active_items)

    # Inactive users
    inactive_resp = client.get("/users/?is_active=false")
    assert inactive_resp.status_code == 200
    inactive_items = inactive_resp.json()
    assert len(inactive_items) == 1
    assert inactive_items[0]["id"] == first_id
    assert inactive_items[0]["is_active"] is False


def test_combined_filters_and_pagination(client: TestClient) -> None:
    """Verify role, search, is_active, limit, and offset work cohesively."""
    _seed_sample_users(client)

    # Enterprise + corp search gives 2 records; pagination limit=1, offset=1 gives second record
    page_1 = client.get("/users/?role=enterprise&search=corp&limit=1&offset=0")
    assert page_1.status_code == 200
    items_1 = page_1.json()
    assert len(items_1) == 1
    assert items_1[0]["username"] == "charlie_corp"

    page_2 = client.get("/users/?role=enterprise&search=corp&limit=1&offset=1")
    assert page_2.status_code == 200
    items_2 = page_2.json()
    assert len(items_2) == 1
    assert items_2[0]["username"] == "dana_corp"


# ==========================================
# 6. OpenAPI Documentation Introspection
# ==========================================


def test_openapi_schema_contains_parameter_metadata(client: TestClient) -> None:
    """Verify FastAPI auto-generates OpenAPI documentation reflecting Path and Query constraints."""
    response = client.get("/openapi.json")
    assert response.status_code == 200
    schema = response.json()

    # Verify /users/{user_id} path parameter metadata
    get_user_params = schema["paths"]["/users/{user_id}"]["get"]["parameters"]
    user_id_param = next(p for p in get_user_params if p["name"] == "user_id")
    assert user_id_param["in"] == "path"
    assert user_id_param["required"] is True
    assert user_id_param["schema"]["minimum"] == 1
    assert user_id_param["schema"]["maximum"] == 2_147_483_647

    # Verify /users/by-username/{username} path parameter metadata
    by_username_params = schema["paths"]["/users/by-username/{username}"]["get"]["parameters"]
    username_param = next(p for p in by_username_params if p["name"] == "username")
    assert username_param["in"] == "path"
    assert username_param["required"] is True
    assert username_param["schema"]["minLength"] == 3
    assert username_param["schema"]["maxLength"] == 50
    assert username_param["schema"]["pattern"] == "^[a-z0-9_]+$"

    # Verify /users/ query parameter constraints
    list_params = schema["paths"]["/users/"]["get"]["parameters"]
    limit_param = next(p for p in list_params if p["name"] == "limit")
    assert limit_param["in"] == "query"
    assert limit_param["schema"]["minimum"] == 1
    assert limit_param["schema"]["maximum"] == 100

    search_param = next(p for p in list_params if p["name"] == "search")
    assert search_param["in"] == "query"
    # In OpenAPI 3.1.0 (FastAPI + Pydantic v2), nullable params use anyOf
    search_schema = search_param["schema"].get("anyOf", [search_param["schema"]])[0]
    assert search_schema["minLength"] == 2
    assert search_schema["maxLength"] == 50
    assert search_schema["pattern"] == "^[a-zA-Z0-9_ ]+$"
