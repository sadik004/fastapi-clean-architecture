"""Unit tests for Pydantic v2 schemas and validation pipelines."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.repositories.user_repository import UserEntity
from app.schemas.user import UserCreate, UserProfileUpdate, UserResponse, UserRole


def test_user_create_valid_minimal() -> None:
    """Verify UserCreate succeeds with valid required fields and default values."""
    user = UserCreate(
        email="jane.doe@example.com",
        username="jane_doe",
        password="strongpassword123",
        password_confirm="strongpassword123",
    )
    assert user.email == "jane.doe@example.com"
    assert user.username == "jane_doe"
    assert user.password == "strongpassword123"
    assert user.age is None
    assert user.role == UserRole.USER


def test_user_create_valid_full() -> None:
    """Verify UserCreate succeeds with all optional fields provided."""
    user = UserCreate(
        email="admin.user@example.com",
        username="admin_99",
        password="SecurePassword#2026",
        password_confirm="SecurePassword#2026",
        age=30,
        role=UserRole.ADMIN,
    )
    assert user.age == 30
    assert user.role == UserRole.ADMIN


@pytest.mark.parametrize(
    "invalid_username",
    [
        "ab",  # Too short (< 3 chars)
        "a" * 51,  # Too long (> 50 chars)
        "invalid user",  # Space not allowed
        "user@domain",  # @ not allowed
        "user-name",  # Dash not allowed
        "user!name",  # Special characters not allowed
    ],
)
def test_user_create_invalid_username(invalid_username: str) -> None:
    """Verify UserCreate rejects invalid username lengths and patterns."""
    with pytest.raises(ValidationError) as exc_info:
        UserCreate(
            email="valid@example.com",
            username=invalid_username,
            password="validpassword123",
            password_confirm="validpassword123",
        )
    assert "username" in str(exc_info.value)


@pytest.mark.parametrize(
    "invalid_email",
    [
        "not-an-email",
        "missingatsign.com",
        "@missinglocal.com",
        "spaces in@email.com",
    ],
)
def test_user_create_invalid_email(invalid_email: str) -> None:
    """Verify UserCreate rejects invalid email addresses."""
    with pytest.raises(ValidationError) as exc_info:
        UserCreate(
            email=invalid_email,
            username="valid_user",
            password="validpassword123",
            password_confirm="validpassword123",
        )
    assert "email" in str(exc_info.value)


@pytest.mark.parametrize("invalid_age", [17, 121, -5, 0])
def test_user_create_invalid_age(invalid_age: int) -> None:
    """Verify UserCreate rejects ages outside [18, 120]."""
    with pytest.raises(ValidationError) as exc_info:
        UserCreate(
            email="valid@example.com",
            username="valid_user",
            password="validpassword123",
            password_confirm="validpassword123",
            age=invalid_age,
        )
    assert "age" in str(exc_info.value)


@pytest.mark.parametrize("valid_age", [18, 25, 65, 120])
def test_user_create_valid_age_boundaries(valid_age: int) -> None:
    """Verify UserCreate accepts boundary ages 18 and 120."""
    user = UserCreate(
        email="valid@example.com",
        username="valid_user",
        password="validpassword123",
        password_confirm="validpassword123",
        age=valid_age,
    )
    assert user.age == valid_age


def test_user_create_invalid_password_length() -> None:
    """Verify UserCreate rejects passwords shorter than 8 characters."""
    with pytest.raises(ValidationError) as exc_info:
        UserCreate(
            email="valid@example.com",
            username="valid_user",
            password="short",
            password_confirm="short",
        )
    assert "password" in str(exc_info.value)


def test_user_create_invalid_role() -> None:
    """Verify UserCreate rejects unrecognized roles."""
    with pytest.raises(ValidationError) as exc_info:
        UserCreate(
            email="valid@example.com",
            username="valid_user",
            password="validpassword123",
            password_confirm="validpassword123",
            role="superadmin",  # type: ignore[arg-type]
        )
    assert "role" in str(exc_info.value)


def test_user_profile_update_partial_valid() -> None:
    """Verify UserProfileUpdate allows updating username or age independently."""
    update_username = UserProfileUpdate(username="new_username_42")
    assert update_username.username == "new_username_42"
    assert update_username.age is None

    update_age = UserProfileUpdate(age=25)
    assert update_age.username is None
    assert update_age.age == 25

    empty_update = UserProfileUpdate()
    assert empty_update.username is None
    assert empty_update.age is None


def test_user_profile_update_invalid_fields() -> None:
    """Verify UserProfileUpdate validates field constraints when values are provided."""
    with pytest.raises(ValidationError):
        UserProfileUpdate(username="ab")  # Too short

    with pytest.raises(ValidationError):
        UserProfileUpdate(username="invalid name!")  # Invalid pattern

    with pytest.raises(ValidationError):
        UserProfileUpdate(age=16)  # Underage


def test_user_response_excludes_sensitive_fields() -> None:
    """Verify UserResponse never exposes password, password_hash, or password_confirm."""
    entity = UserEntity(
        id=1,
        email="architect@example.com",
        username="architect_01",
        password_hash="argon2_secret_hash_value_12345",
        is_active=True,
        created_at=datetime.now(UTC),
        age=32,
        role="user",
        company_name="Tech Corp",
    )

    response = UserResponse.model_validate(entity)
    dumped = response.model_dump()

    assert dumped["id"] == 1
    assert dumped["email"] == "architect@example.com"
    assert dumped["username"] == "architect_01"
    assert dumped["age"] == 32
    assert dumped["role"] == UserRole.USER
    assert dumped["company_name"] == "Tech Corp"
    assert dumped["is_active"] is True
    assert "created_at" in dumped

    # Strict guarantee: sensitive and transient fields are not in the response model or dump
    assert "password" not in dumped
    assert "password_hash" not in dumped
    assert "password_confirm" not in dumped
    assert not hasattr(response, "password")
    assert not hasattr(response, "password_hash")
    assert not hasattr(response, "password_confirm")
