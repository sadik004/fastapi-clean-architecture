"""Pydantic v2 schemas for authentication, token exchange, and refresh token rotation."""

from pydantic import BaseModel, ConfigDict, Field


class LoginRequest(BaseModel):
    """User credentials for standard username/password login."""

    username: str = Field(..., min_length=1, max_length=100, description="Username or email address")
    password: str = Field(..., min_length=1, max_length=128, description="Plaintext password")

    model_config = ConfigDict(extra="forbid")


class TokenResponse(BaseModel):
    """Standardized OAuth2-compatible token pair response."""

    access_token: str = Field(..., description="Short-lived stateless JWT access token")
    refresh_token: str = Field(..., description="Long-lived rotation-tracked JWT refresh token")
    token_type: str = Field(default="bearer", description="Token type header prefix")
    expires_in: int = Field(default=900, description="Access token expiration window in seconds")

    model_config = ConfigDict(from_attributes=True)


class RefreshTokenRequest(BaseModel):
    """Payload for rotating refresh token and issuing a new token pair."""

    refresh_token: str = Field(..., min_length=10, description="Valid signed JWT refresh token")

    model_config = ConfigDict(extra="forbid")


class AuthenticatedUserResponse(BaseModel):
    """Stateless claims projection for currently authenticated client."""

    user_id: int = Field(..., description="Unique authenticated user integer ID")
    role: str = Field(..., description="User access control role")

    model_config = ConfigDict(from_attributes=True)


class LogoutResponse(BaseModel):
    """Session invalidation response."""

    message: str = Field(default="Successfully logged out", description="Confirmation status message")
