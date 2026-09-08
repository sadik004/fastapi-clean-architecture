"""Pydantic v2 schemas for User domain with strict field constraints and DTO separation."""

from datetime import datetime
from enum import Enum
from typing import Optional
from pydantic import BaseModel, ConfigDict, EmailStr, Field


class UserRole(str, Enum):
    """User authorization roles."""

    USER = "user"
    ADMIN = "admin"


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


class UserCreate(UserBase):
    """Input payload schema for creating a new user."""

    password: str = Field(
        ...,
        min_length=8,
        max_length=128,
        description="Plaintext password for registration (minimum 8 characters)",
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


class UserProfileUpdate(BaseModel):
    """Input payload schema for updating user profile fields."""

    username: Optional[str] = Field(
        default=None,
        min_length=3,
        max_length=50,
        pattern=r"^[a-zA-Z0-9_]+$",
        description="Updated username containing only alphanumeric characters and underscores",
    )
    age: Optional[int] = Field(
        default=None,
        ge=18,
        le=120,
        description="Updated user age (must be between 18 and 120)",
    )


class UserResponse(UserBase):
    """Output response schema for returning user data.

    Sensitive internal fields (e.g. password, password_hash) are strictly excluded.
    """

    id: int = Field(..., description="Unique identifier of the user")
    age: Optional[int] = Field(default=None, description="User age")
    role: UserRole = Field(default=UserRole.USER, description="User role")
    is_active: bool = Field(default=True, description="Account active status")
    created_at: datetime = Field(..., description="Timestamp of user creation")

    model_config = ConfigDict(from_attributes=True)
