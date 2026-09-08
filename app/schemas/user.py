"""Pydantic v2 schemas for User domain with custom @field_validator and @model_validator rules."""

from datetime import datetime
from enum import Enum
import re
from typing import Any, Optional, Pattern, Self
from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator

# Module-level pre-compiled regexes & sets for O(1) lookups and O(k) pattern matching
RESERVED_USERNAMES: frozenset[str] = frozenset(
    {"admin", "root", "system", "superuser", "administrator", "operator"}
)
RE_E164_PHONE: Pattern[str] = re.compile(r"^\+[1-9]\d{1,14}$")
RE_HTML_TAGS: Pattern[str] = re.compile(r"<[^<]+?>")


def sanitize_username_before(v: Any) -> Any:
    """Strip whitespace and lowercase raw username input (mode='before')."""
    if isinstance(v, str):
        return v.strip().lower()
    return v


def validate_username_after(v: Optional[str]) -> Optional[str]:
    """Enforce invariants: reject consecutive underscores and reserved system keywords (mode='after')."""
    if v is None:
        return v
    if "__" in v:
        raise ValueError("Username cannot contain consecutive underscores ('__').")
    if v in RESERVED_USERNAMES:
        raise ValueError(f"Username '{v}' is a reserved system keyword.")
    return v


def sanitize_full_name_before(v: Any) -> Any:
    """Strip, collapse multiple whitespace characters into single space, and Title Case (mode='before')."""
    if isinstance(v, str):
        collapsed = " ".join(v.split())
        return collapsed.title() if collapsed else None
    return v


def sanitize_phone_number_before(v: Any) -> Any:
    """Trim whitespace from phone number (mode='before')."""
    if isinstance(v, str):
        cleaned = v.strip()
        return cleaned if cleaned else None
    return v


def validate_phone_number_after(v: Optional[str]) -> Optional[str]:
    """Validate phone conforms to international E.164 format via pre-compiled regex (mode='after')."""
    if v is not None and not RE_E164_PHONE.match(v):
        raise ValueError(
            "Phone number must conform to international E.164 format (e.g. +1234567890)."
        )
    return v


def sanitize_bio_before(v: Any) -> Any:
    """Strip dangerous HTML tags to defend against XSS injection (mode='before')."""
    if isinstance(v, str):
        cleaned = RE_HTML_TAGS.sub("", v).strip()
        return cleaned if cleaned else None
    return v


def sanitize_company_name_before(v: Any) -> Any:
    """Strip whitespace from company name (mode='before')."""
    if isinstance(v, str):
        cleaned = v.strip()
        return cleaned if cleaned else None
    return v


class UserRole(str, Enum):
    """User authorization roles."""

    USER = "user"
    ADMIN = "admin"
    ENTERPRISE = "enterprise"


class UserBase(BaseModel):
    """Base user attributes shared across schemas."""

    email: EmailStr = Field(
        ...,
        description="Unique email address of the user",
    )
    username: str = Field(
        ...,
        min_length=3,
        max_length=50,
        pattern=r"^[a-zA-Z0-9_]+$",
        description="Unique username containing only alphanumeric characters and underscores",
    )
    full_name: Optional[str] = Field(
        default=None,
        max_length=100,
        description="User's full name, auto-normalized to Title Case",
    )
    phone_number: Optional[str] = Field(
        default=None,
        description="International phone number conforming to E.164 format",
    )
    bio: Optional[str] = Field(
        default=None,
        max_length=500,
        description="User biography with HTML tags automatically stripped",
    )
    company_name: Optional[str] = Field(
        default=None,
        max_length=100,
        description="Organization or company name for enterprise accounts",
    )

    @field_validator("username", mode="before")
    @classmethod
    def normalize_username(cls, v: Any) -> Any:
        return sanitize_username_before(v)

    @field_validator("username", mode="after")
    @classmethod
    def check_username_invariants(cls, v: str) -> str:
        res = validate_username_after(v)
        assert res is not None
        return res

    @field_validator("full_name", mode="before")
    @classmethod
    def normalize_full_name(cls, v: Any) -> Any:
        return sanitize_full_name_before(v)

    @field_validator("phone_number", mode="before")
    @classmethod
    def normalize_phone_number(cls, v: Any) -> Any:
        return sanitize_phone_number_before(v)

    @field_validator("phone_number", mode="after")
    @classmethod
    def validate_phone(cls, v: Optional[str]) -> Optional[str]:
        return validate_phone_number_after(v)

    @field_validator("bio", mode="before")
    @classmethod
    def sanitize_bio(cls, v: Any) -> Any:
        return sanitize_bio_before(v)

    @field_validator("company_name", mode="before")
    @classmethod
    def normalize_company_name(cls, v: Any) -> Any:
        return sanitize_company_name_before(v)


class UserCreate(UserBase):
    """Input payload schema for creating a new user."""

    password: str = Field(
        ...,
        min_length=8,
        max_length=128,
        description="Plaintext password for registration (minimum 8 characters)",
    )
    password_confirm: str = Field(
        ...,
        min_length=8,
        max_length=128,
        description="Password confirmation that must match password identically",
    )
    age: Optional[int] = Field(
        default=None,
        ge=18,
        le=120,
        description="Optional user age (must be between 18 and 120)",
    )
    role: UserRole = Field(
        default=UserRole.USER,
        description="Assigned user role",
    )

    @model_validator(mode="after")
    def validate_cross_field_invariants(self) -> Self:
        """Enforce cross-field business invariants in O(1) space."""
        # Invariant 1: Password Confirmation Match
        if self.password != self.password_confirm:
            raise ValueError("Passwords do not match.")

        # Invariant 2: Credential Integrity (password must not contain username)
        if self.username.lower() in self.password.lower():
            raise ValueError("Password must not contain the username.")

        # Invariant 3: Conditional Role Requirements (Enterprise requires company_name)
        if self.role == UserRole.ENTERPRISE and (
            not self.company_name or not self.company_name.strip()
        ):
            raise ValueError("company_name is strictly required when role is 'enterprise'.")

        return self


class UserUpdate(BaseModel):
    """Input payload schema for updating user details."""

    email: Optional[EmailStr] = Field(
        default=None,
        description="Updated email address",
    )
    username: Optional[str] = Field(
        default=None,
        min_length=3,
        max_length=50,
        pattern=r"^[a-zA-Z0-9_]+$",
        description="Updated username containing only alphanumeric characters and underscores",
    )
    full_name: Optional[str] = Field(
        default=None,
        max_length=100,
        description="Updated full name, auto-normalized to Title Case",
    )
    phone_number: Optional[str] = Field(
        default=None,
        description="Updated international phone number conforming to E.164 format",
    )
    bio: Optional[str] = Field(
        default=None,
        max_length=500,
        description="Updated user biography with HTML tags automatically stripped",
    )
    company_name: Optional[str] = Field(
        default=None,
        max_length=100,
        description="Updated organization or company name",
    )
    age: Optional[int] = Field(
        default=None,
        ge=18,
        le=120,
        description="Updated user age (must be between 18 and 120)",
    )
    role: Optional[UserRole] = Field(
        default=None,
        description="Updated user role",
    )

    @field_validator("username", mode="before")
    @classmethod
    def normalize_username(cls, v: Any) -> Any:
        return sanitize_username_before(v)

    @field_validator("username", mode="after")
    @classmethod
    def check_username_invariants(cls, v: Optional[str]) -> Optional[str]:
        return validate_username_after(v)

    @field_validator("full_name", mode="before")
    @classmethod
    def normalize_full_name(cls, v: Any) -> Any:
        return sanitize_full_name_before(v)

    @field_validator("phone_number", mode="before")
    @classmethod
    def normalize_phone_number(cls, v: Any) -> Any:
        return sanitize_phone_number_before(v)

    @field_validator("phone_number", mode="after")
    @classmethod
    def validate_phone(cls, v: Optional[str]) -> Optional[str]:
        return validate_phone_number_after(v)

    @field_validator("bio", mode="before")
    @classmethod
    def sanitize_bio(cls, v: Any) -> Any:
        return sanitize_bio_before(v)

    @field_validator("company_name", mode="before")
    @classmethod
    def normalize_company_name(cls, v: Any) -> Any:
        return sanitize_company_name_before(v)


class UserProfileUpdate(BaseModel):
    """Input payload schema for updating user profile fields."""

    username: Optional[str] = Field(
        default=None,
        min_length=3,
        max_length=50,
        pattern=r"^[a-zA-Z0-9_]+$",
        description="Updated username containing only alphanumeric characters and underscores",
    )
    full_name: Optional[str] = Field(
        default=None,
        max_length=100,
        description="Updated full name, auto-normalized to Title Case",
    )
    phone_number: Optional[str] = Field(
        default=None,
        description="Updated international phone number conforming to E.164 format",
    )
    bio: Optional[str] = Field(
        default=None,
        max_length=500,
        description="Updated user biography with HTML tags automatically stripped",
    )
    company_name: Optional[str] = Field(
        default=None,
        max_length=100,
        description="Updated organization or company name",
    )
    age: Optional[int] = Field(
        default=None,
        ge=18,
        le=120,
        description="Updated user age (must be between 18 and 120)",
    )

    @field_validator("username", mode="before")
    @classmethod
    def normalize_username(cls, v: Any) -> Any:
        return sanitize_username_before(v)

    @field_validator("username", mode="after")
    @classmethod
    def check_username_invariants(cls, v: Optional[str]) -> Optional[str]:
        return validate_username_after(v)

    @field_validator("full_name", mode="before")
    @classmethod
    def normalize_full_name(cls, v: Any) -> Any:
        return sanitize_full_name_before(v)

    @field_validator("phone_number", mode="before")
    @classmethod
    def normalize_phone_number(cls, v: Any) -> Any:
        return sanitize_phone_number_before(v)

    @field_validator("phone_number", mode="after")
    @classmethod
    def validate_phone(cls, v: Optional[str]) -> Optional[str]:
        return validate_phone_number_after(v)

    @field_validator("bio", mode="before")
    @classmethod
    def sanitize_bio(cls, v: Any) -> Any:
        return sanitize_bio_before(v)

    @field_validator("company_name", mode="before")
    @classmethod
    def normalize_company_name(cls, v: Any) -> Any:
        return sanitize_company_name_before(v)


class UserResponse(UserBase):
    """Output response schema for returning user data.

    Sensitive internal fields (e.g. password, password_hash) and transient validation
    fields (e.g. password_confirm) are strictly excluded.
    """

    id: int = Field(..., description="Unique identifier of the user")
    age: Optional[int] = Field(default=None, description="User age")
    role: UserRole = Field(default=UserRole.USER, description="User role")
    is_active: bool = Field(default=True, description="Account active status")
    created_at: datetime = Field(..., description="Timestamp of user creation")

    model_config = ConfigDict(from_attributes=True)


class UserDashboardResponse(BaseModel):
    """Aggregated dashboard projection fetched concurrently across multiple I/O sources."""

    profile: UserResponse = Field(..., description="User profile details")
    activity_logs: list[dict[str, Any]] = Field(
        default_factory=list, description="Recent user activity and transaction logs"
    )
    stats: dict[str, Any] = Field(
        default_factory=dict, description="Account metrics and usage statistics"
    )

    model_config = ConfigDict(from_attributes=True)


class UserReportResponse(BaseModel):
    """Output projection for CPU-heavy data analytics and report export."""

    user_id: int = Field(..., description="Unique identifier of the user")
    username: str = Field(..., description="Normalized username")
    report_checksum: str = Field(
        ..., description="Cryptographic integrity checksum of generated dataset"
    )
    records_processed: int = Field(..., description="Total synthetic records analyzed")
    generated_at: datetime = Field(..., description="Timestamp of report completion")

    model_config = ConfigDict(from_attributes=True)

