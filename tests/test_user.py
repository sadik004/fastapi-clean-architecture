"""Comprehensive integration and endpoint test suite for User domain."""

import pytest
from fastapi.testclient import TestClient


def test_health_check(client: TestClient) -> None:
    """Verify health check endpoint returns 200 OK."""
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "healthy"}


def test_create_user_success(client: TestClient) -> None:
    """Verify successful user creation returns 201 Created and matches UserResponse."""
    payload = {
        "email": "lead.architect@example.com",
        "username": "lead_architect",
        "password": "SuperSecretPassword123",
        "password_confirm": "SuperSecretPassword123",
        "age": 28,
        "role": "admin",
    }
    response = client.post("/users/", json=payload)
    assert response.status_code == 201
    data = response.json()
    assert data["id"] == 1
    assert data["email"] == payload["email"]
    assert data["username"] == payload["username"]
    assert data["age"] == 28
    assert data["role"] == "admin"
    assert data["is_active"] is True
    assert "created_at" in data

    # Strict sensitive and transient field exclusion guarantee
    assert "password" not in data
    assert "password_hash" not in data
    assert "password_confirm" not in data


def test_create_user_duplicate_email(client: TestClient) -> None:
    """Verify creating a user with duplicate email yields 409 Conflict."""
    payload = {
        "email": "duplicate@example.com",
        "username": "user_one",
        "password": "ValidPassword123!",
        "password_confirm": "ValidPassword123!",
    }
    first_resp = client.post("/users/", json=payload)
    assert first_resp.status_code == 201

    duplicate_payload = {
        "email": "duplicate@example.com",
        "username": "user_two",
        "password": "ValidPassword123!",
        "password_confirm": "ValidPassword123!",
    }
    second_resp = client.post("/users/", json=duplicate_payload)
    assert second_resp.status_code == 409
    assert "already registered" in second_resp.json()["detail"]


def test_create_user_duplicate_username(client: TestClient) -> None:
    """Verify creating a user with duplicate username yields 409 Conflict."""
    payload = {
        "email": "first@example.com",
        "username": "same_handle",
        "password": "ValidPassword123!",
        "password_confirm": "ValidPassword123!",
    }
    first_resp = client.post("/users/", json=payload)
    assert first_resp.status_code == 201

    duplicate_payload = {
        "email": "second@example.com",
        "username": "same_handle",
        "password": "ValidPassword123!",
        "password_confirm": "ValidPassword123!",
    }
    second_resp = client.post("/users/", json=duplicate_payload)
    assert second_resp.status_code == 409
    assert "already taken" in second_resp.json()["detail"]


@pytest.mark.parametrize(
    ("field", "invalid_val"),
    [
        ("email", "not-an-email"),
        ("username", "ab"),  # Too short (<3)
        ("username", "bad handle!"),  # Invalid characters
        ("password", "short"),  # Too short (<8)
        ("age", 15),  # Underage (<18)
        ("age", 130),  # Overage (>120)
    ],
)
def test_create_user_validation_failures(
    client: TestClient, field: str, invalid_val: object
) -> None:
    """Verify Pydantic validation violations return 422 Unprocessable Entity."""
    payload = {
        "email": "valid@example.com",
        "username": "valid_user",
        "password": "ValidPassword123!",
        "password_confirm": "ValidPassword123!",
        "age": 25,
        field: invalid_val,
    }
    response = client.post("/users/", json=payload)
    assert response.status_code == 422
    errors = response.json().get("detail", [])
    assert any(err.get("loc")[-1] == field for err in errors)


def test_get_user_by_id_success(client: TestClient) -> None:
    """Verify fetching an existing user by ID returns 200 OK without sensitive fields."""
    create_resp = client.post(
        "/users/",
        json={
            "email": "fetch@example.com",
            "username": "fetch_user",
            "password": "ValidPassword123!",
            "password_confirm": "ValidPassword123!",
        },
    )
    assert create_resp.status_code == 201
    user_id = create_resp.json()["id"]

    get_resp = client.get(f"/users/{user_id}")
    assert get_resp.status_code == 200
    data = get_resp.json()
    assert data["id"] == user_id
    assert data["email"] == "fetch@example.com"
    assert "password" not in data
    assert "password_hash" not in data
    assert "password_confirm" not in data


def test_get_user_by_id_not_found(client: TestClient) -> None:
    """Verify fetching a non-existent user returns 404 Not Found."""
    response = client.get("/users/9999")
    assert response.status_code == 404
    assert "not found" in response.json()["detail"]


def test_patch_user_profile_success(client: TestClient) -> None:
    """Verify partial profile update via PATCH /users/{user_id}."""
    create_resp = client.post(
        "/users/",
        json={
            "email": "profile@example.com",
            "username": "original_name",
            "password": "ValidPassword123!",
            "password_confirm": "ValidPassword123!",
            "age": 21,
        },
    )
    user_id = create_resp.json()["id"]

    patch_resp = client.patch(
        f"/users/{user_id}",
        json={"username": "updated_name", "age": 22},
    )
    assert patch_resp.status_code == 200
    data = patch_resp.json()
    assert data["username"] == "updated_name"
    assert data["age"] == 22
    assert "password" not in data
    assert "password_hash" not in data
    assert "password_confirm" not in data


def test_patch_user_profile_validation_error(client: TestClient) -> None:
    """Verify PATCH with invalid constraints returns 422."""
    create_resp = client.post(
        "/users/",
        json={
            "email": "profile_val@example.com",
            "username": "profile_val",
            "password": "ValidPassword123!",
            "password_confirm": "ValidPassword123!",
        },
    )
    user_id = create_resp.json()["id"]

    patch_resp = client.patch(f"/users/{user_id}", json={"age": 16})
    assert patch_resp.status_code == 422


def test_patch_user_profile_duplicate_username(client: TestClient) -> None:
    """Verify updating username to an already existing username returns 409 Conflict."""
    client.post(
        "/users/",
        json={
            "email": "user1@example.com",
            "username": "taken_handle",
            "password": "ValidPassword123!",
            "password_confirm": "ValidPassword123!",
        },
    )
    second_user = client.post(
        "/users/",
        json={
            "email": "user2@example.com",
            "username": "second_handle",
            "password": "ValidPassword123!",
            "password_confirm": "ValidPassword123!",
        },
    ).json()

    patch_resp = client.patch(
        f"/users/{second_user['id']}",
        json={"username": "taken_handle"},
    )
    assert patch_resp.status_code == 409
    assert "already taken" in patch_resp.json()["detail"]


def test_patch_user_profile_not_found(client: TestClient) -> None:
    """Verify updating a non-existent user returns 404 Not Found."""
    patch_resp = client.patch("/users/9999", json={"age": 30})
    assert patch_resp.status_code == 404


def test_list_users(client: TestClient) -> None:
    """Verify listing all users returns a list of registered users."""
    client.post(
        "/users/",
        json={
            "email": "u1@example.com",
            "username": "user1",
            "password": "ValidPassword123!",
            "password_confirm": "ValidPassword123!",
        },
    )
    client.post(
        "/users/",
        json={
            "email": "u2@example.com",
            "username": "user2",
            "password": "ValidPassword123!",
            "password_confirm": "ValidPassword123!",
        },
    )

    response = client.get("/users/")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 2
    assert {u["username"] for u in data} == {"user1", "user2"}
    for u in data:
        assert "password" not in u
        assert "password_hash" not in u
        assert "password_confirm" not in u


def test_create_user_with_sanitized_inputs(client: TestClient) -> None:
    """Verify endpoint receives, sanitizes, and returns normalized field data."""
    payload = {
        "email": "sanitized@example.com",
        "username": "   SuperCoder_99   ",
        "password": "SecurePassword123!",
        "password_confirm": "SecurePassword123!",
        "full_name": "  alan     mathison   turing  ",
        "phone_number": "   +14155552671   ",
        "bio": "<script>alert('xss')</script>Pioneer in <b>Computer Science</b>.",
    }
    response = client.post("/users/", json=payload)
    assert response.status_code == 201
    data = response.json()
    assert data["username"] == "supercoder_99"
    assert data["full_name"] == "Alan Mathison Turing"
    assert data["phone_number"] == "+14155552671"
    assert data["bio"] == "alert('xss')Pioneer in Computer Science."

    # Verify persistence layer preserves sanitized state
    get_resp = client.get(f"/users/{data['id']}")
    assert get_resp.status_code == 200
    get_data = get_resp.json()
    assert get_data["username"] == "supercoder_99"
    assert get_data["full_name"] == "Alan Mathison Turing"
    assert get_data["bio"] == "alert('xss')Pioneer in Computer Science."


@pytest.mark.parametrize("reserved_name", ["admin", "root", "  SYSTEM  ", "superuser"])
def test_create_user_rejects_reserved_username(
    client: TestClient, reserved_name: str
) -> None:
    """Verify POST /users/ rejects reserved system usernames with HTTP 422."""
    payload = {
        "email": "reserved@example.com",
        "username": reserved_name,
        "password": "SecurePassword123!",
        "password_confirm": "SecurePassword123!",
    }
    response = client.post("/users/", json=payload)
    assert response.status_code == 422
    assert "reserved system keyword" in response.text


def test_create_user_rejects_consecutive_underscores(client: TestClient) -> None:
    """Verify POST /users/ rejects consecutive underscores with HTTP 422."""
    payload = {
        "email": "underscores@example.com",
        "username": "invalid__handle",
        "password": "SecurePassword123!",
        "password_confirm": "SecurePassword123!",
    }
    response = client.post("/users/", json=payload)
    assert response.status_code == 422
    assert "consecutive underscores" in response.text


def test_create_user_rejects_invalid_phone(client: TestClient) -> None:
    """Verify POST /users/ rejects non-E.164 phone formats with HTTP 422."""
    payload = {
        "email": "badphone@example.com",
        "username": "valid_user",
        "password": "SecurePassword123!",
        "password_confirm": "SecurePassword123!",
        "phone_number": "14155552671",  # missing leading +
    }
    response = client.post("/users/", json=payload)
    assert response.status_code == 422
    assert "E.164" in response.text


def test_create_user_password_mismatch_returns_422(client: TestClient) -> None:
    """Verify POST /users/ returns HTTP 422 when password and password_confirm do not match."""
    payload = {
        "email": "mismatch@example.com",
        "username": "mismatch_user",
        "password": "SecurePassword123!",
        "password_confirm": "DifferentPassword123!",
    }
    response = client.post("/users/", json=payload)
    assert response.status_code == 422
    assert "Passwords do not match." in response.text


def test_create_user_password_containing_username_returns_422(client: TestClient) -> None:
    """Verify POST /users/ returns HTTP 422 when password contains username."""
    payload = {
        "email": "trivial@example.com",
        "username": "john_coder",
        "password": "Super_john_coder_123!",
        "password_confirm": "Super_john_coder_123!",
    }
    response = client.post("/users/", json=payload)
    assert response.status_code == 422
    assert "Password must not contain the username." in response.text


def test_create_user_enterprise_requires_company_name(client: TestClient) -> None:
    """Verify POST /users/ returns HTTP 422 when role=enterprise without company_name."""
    payload = {
        "email": "enterprise@example.com",
        "username": "enterprise_user",
        "password": "SecurePassword123!",
        "password_confirm": "SecurePassword123!",
        "role": "enterprise",
    }
    response = client.post("/users/", json=payload)
    assert response.status_code == 422
    assert "company_name is strictly required when role is 'enterprise'." in response.text


def test_create_user_enterprise_with_company_name_succeeds(client: TestClient) -> None:
    """Verify POST /users/ returns HTTP 201 when role=enterprise with company_name."""
    payload = {
        "email": "corp@example.com",
        "username": "corp_admin",
        "password": "SecurePassword123!",
        "password_confirm": "SecurePassword123!",
        "role": "enterprise",
        "company_name": "Antigravity Systems Inc.",
    }
    response = client.post("/users/", json=payload)
    assert response.status_code == 201
    data = response.json()
    assert data["role"] == "enterprise"
    assert data["company_name"] == "Antigravity Systems Inc."
    assert "password_confirm" not in data
