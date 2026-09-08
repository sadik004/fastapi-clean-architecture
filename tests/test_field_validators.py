"""Unit tests for Pydantic v2 custom @field_validator rules, sanitization, and business invariants."""

import pytest
from pydantic import ValidationError

from app.schemas.user import UserCreate, UserProfileUpdate, UserUpdate


def test_username_auto_normalization() -> None:
    """Verify raw username input with whitespace and uppercase is normalized (mode='before')."""
    user = UserCreate(
        email="dev@example.com",
        username="  JohnDoe_Dev  ",
        password="SecurePassword123!",
        password_confirm="SecurePassword123!",
    )
    assert user.username == "johndoe_dev"


def test_username_rejects_consecutive_underscores() -> None:
    """Verify username rejects consecutive underscores ('__') (mode='after')."""
    with pytest.raises(ValidationError) as exc_info:
        UserCreate(
            email="dev@example.com",
            username="john__doe",
            password="SecurePassword123!",
            password_confirm="SecurePassword123!",
        )
    assert "consecutive underscores" in str(exc_info.value)


@pytest.mark.parametrize(
    "reserved_name",
    ["admin", "root", "system", "superuser", "administrator", "operator"],
)
def test_username_rejects_reserved_keywords(reserved_name: str) -> None:
    """Verify username rejects reserved system keywords with O(1) frozenset check (mode='after')."""
    with pytest.raises(ValidationError) as exc_info:
        UserCreate(
            email="dev@example.com",
            username=reserved_name,
            password="SecurePassword123!",
            password_confirm="SecurePassword123!",
        )
    assert "reserved system keyword" in str(exc_info.value)


def test_username_rejects_reserved_keywords_with_mixed_case_and_padding() -> None:
    """Verify mode='before' lowercases/trims before mode='after' checks reserved words."""
    with pytest.raises(ValidationError) as exc_info:
        UserCreate(
            email="dev@example.com",
            username="   ADMIN   ",
            password="SecurePassword123!",
            password_confirm="SecurePassword123!",
        )
    assert "reserved system keyword" in str(exc_info.value)


def test_full_name_whitespace_collapsing_and_title_casing() -> None:
    """Verify full_name collapses multiple spaces and applies Title Case (mode='before')."""
    user = UserCreate(
        email="ada@example.com",
        username="ada_lovelace",
        password="SecurePassword123!",
        password_confirm="SecurePassword123!",
        full_name="   ada     marie   lovelace   ",
    )
    assert user.full_name == "Ada Marie Lovelace"


def test_full_name_empty_whitespace_becomes_none() -> None:
    """Verify full_name consisting only of whitespace normalizes to None."""
    user = UserCreate(
        email="ada@example.com",
        username="ada_lovelace",
        password="SecurePassword123!",
        password_confirm="SecurePassword123!",
        full_name="     ",
    )
    assert user.full_name is None


@pytest.mark.parametrize(
    "valid_phone",
    [
        "+14155552671",
        "+442071838750",
        "+8801712345678",
        "  +14155552671  ",  # with leading/trailing whitespace
    ],
)
def test_phone_number_e164_valid(valid_phone: str) -> None:
    """Verify phone_number matches E.164 pattern."""
    user = UserCreate(
        email="phone@example.com",
        username="phone_user",
        password="SecurePassword123!",
        password_confirm="SecurePassword123!",
        phone_number=valid_phone,
    )
    assert user.phone_number == valid_phone.strip()


@pytest.mark.parametrize(
    "invalid_phone",
    [
        "14155552671",  # missing leading +
        "+0123456789",  # leading 0 after +
        "+",  # only +
        "+abc1234567",  # letters
        "phone_number",
    ],
)
def test_phone_number_e164_invalid(invalid_phone: str) -> None:
    """Verify invalid phone numbers trigger ValidationError."""
    with pytest.raises(ValidationError) as exc_info:
        UserCreate(
            email="phone@example.com",
            username="phone_user",
            password="SecurePassword123!",
            password_confirm="SecurePassword123!",
            phone_number=invalid_phone,
        )
    assert "E.164" in str(exc_info.value)


def test_bio_html_tag_stripping() -> None:
    """Verify HTML tags are stripped from bio to prevent XSS payloads."""
    xss_bio = "<script>alert('xss')</script>Senior Backend Engineer with <b>Python</b> expertise."
    user = UserCreate(
        email="bio@example.com",
        username="bio_engineer",
        password="SecurePassword123!",
        password_confirm="SecurePassword123!",
        bio=xss_bio,
    )
    assert user.bio == "alert('xss')Senior Backend Engineer with Python expertise."


def test_user_update_validators() -> None:
    """Verify UserUpdate payload also applies sanitization and invariants."""
    update = UserUpdate(
        username="  Clean_Handle  ",
        full_name="  grace   hopper  ",
        phone_number="+15551234567",
        bio="<p>Compiler pioneer</p>",
    )
    assert update.username == "clean_handle"
    assert update.full_name == "Grace Hopper"
    assert update.phone_number == "+15551234567"
    assert update.bio == "Compiler pioneer"

    with pytest.raises(ValidationError):
        UserUpdate(username="system")


def test_user_profile_update_validators() -> None:
    """Verify UserProfileUpdate payload applies sanitization and invariants."""
    profile_update = UserProfileUpdate(
        username="  New_Handle  ",
        full_name="alan   turing",
        bio="<b>Turing Machine</b>",
    )
    assert profile_update.username == "new_handle"
    assert profile_update.full_name == "Alan Turing"
    assert profile_update.bio == "Turing Machine"
