"""Unit tests for Pydantic v2 @model_validator(mode='after') cross-field business invariants."""

import pytest
from pydantic import ValidationError

from app.schemas.user import UserCreate, UserResponse, UserRole


def test_user_create_matching_passwords_succeeds() -> None:
    """Verify registration succeeds when password and password_confirm match identically."""
    user = UserCreate(
        email="developer@example.com",
        username="dev_expert",
        password="StrongPassword123!",
        password_confirm="StrongPassword123!",
    )
    assert user.password == "StrongPassword123!"
    assert user.password_confirm == "StrongPassword123!"


def test_user_create_password_mismatch_raises_error() -> None:
    """Verify registration fails with clear error message when passwords do not match."""
    with pytest.raises(ValidationError) as exc_info:
        UserCreate(
            email="developer@example.com",
            username="dev_expert",
            password="StrongPassword123!",
            password_confirm="CompletelyDifferent123!",
        )
    assert "Passwords do not match." in str(exc_info.value)


def test_user_create_password_containing_username_rejected() -> None:
    """Verify registration fails when password contains username (case-insensitive substring)."""
    with pytest.raises(ValidationError) as exc_info:
        UserCreate(
            email="developer@example.com",
            username="alex_smith",
            password="Super_alex_smith_123!",
            password_confirm="Super_alex_smith_123!",
        )
    assert "Password must not contain the username." in str(exc_info.value)


def test_user_create_password_containing_username_case_insensitive() -> None:
    """Verify credential integrity catches mixed-case username in password."""
    with pytest.raises(ValidationError) as exc_info:
        UserCreate(
            email="developer@example.com",
            username="AlexSmith",
            password="MySecretALEXSMITH#2026",
            password_confirm="MySecretALEXSMITH#2026",
        )
    assert "Password must not contain the username." in str(exc_info.value)


def test_enterprise_role_without_company_name_rejected() -> None:
    """Verify selecting ENTERPRISE role without company_name raises validation error."""
    with pytest.raises(ValidationError) as exc_info:
        UserCreate(
            email="enterprise@example.com",
            username="enterprise_lead",
            password="SecurePassword123!",
            password_confirm="SecurePassword123!",
            role=UserRole.ENTERPRISE,
            company_name=None,
        )
    assert "company_name is strictly required when role is 'enterprise'." in str(exc_info.value)


def test_enterprise_role_with_whitespace_company_name_rejected() -> None:
    """Verify selecting ENTERPRISE role with empty whitespace company_name raises validation error."""
    with pytest.raises(ValidationError) as exc_info:
        UserCreate(
            email="enterprise@example.com",
            username="enterprise_lead",
            password="SecurePassword123!",
            password_confirm="SecurePassword123!",
            role=UserRole.ENTERPRISE,
            company_name="     ",
        )
    assert "company_name is strictly required when role is 'enterprise'." in str(exc_info.value)


def test_enterprise_role_with_valid_company_name_succeeds() -> None:
    """Verify selecting ENTERPRISE role with a non-empty company_name succeeds."""
    user = UserCreate(
        email="enterprise@example.com",
        username="enterprise_lead",
        password="SecurePassword123!",
        password_confirm="SecurePassword123!",
        role=UserRole.ENTERPRISE,
        company_name="Acme Corporation",
    )
    assert user.role == UserRole.ENTERPRISE
    assert user.company_name == "Acme Corporation"


def test_standard_user_without_company_name_succeeds() -> None:
    """Verify standard USER role does not require company_name."""
    user = UserCreate(
        email="standard@example.com",
        username="standard_user",
        password="SecurePassword123!",
        password_confirm="SecurePassword123!",
        role=UserRole.USER,
        company_name=None,
    )
    assert user.role == UserRole.USER
    assert user.company_name is None


def test_transient_field_excluded_from_response() -> None:
    """Verify password_confirm is purely a transient creation artifact and absent from UserResponse."""
    assert "password_confirm" not in UserResponse.model_fields
