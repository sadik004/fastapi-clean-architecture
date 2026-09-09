"""Authentication router exposing login, token refresh with RTR, logout, and stateless profile."""

from typing import Annotated

from fastapi import APIRouter, Depends, status

from app.core.dependencies import get_auth_service, get_current_authenticated_user
from app.schemas.auth import (
    AuthenticatedUserResponse,
    LoginRequest,
    LogoutResponse,
    RefreshTokenRequest,
    TokenResponse,
)
from app.services.auth_service import AuthService

router = APIRouter(prefix="/auth", tags=["Authentication"])


@router.post(
    "/login",
    response_model=TokenResponse,
    status_code=status.HTTP_200_OK,
    summary="Authenticate and issue JWT token pair with active rotation family",
)
async def login(
    payload: LoginRequest,
    auth_service: Annotated[AuthService, Depends(get_auth_service)],
) -> TokenResponse:
    """Validate credentials and issue short-lived access token with rotation-tracked refresh token."""
    return await auth_service.login(payload)


@router.post(
    "/refresh",
    response_model=TokenResponse,
    status_code=status.HTTP_200_OK,
    summary="Rotate refresh token with automated replay theft detection",
)
async def refresh_tokens(
    payload: RefreshTokenRequest,
    auth_service: Annotated[AuthService, Depends(get_auth_service)],
) -> TokenResponse:
    """Exchange valid refresh token for a new token pair, burning the consumed token."""
    return await auth_service.refresh_tokens(payload.refresh_token)


@router.post(
    "/logout",
    response_model=LogoutResponse,
    status_code=status.HTTP_200_OK,
    summary="Revoke refresh token family across the cluster",
)
async def logout(
    payload: RefreshTokenRequest,
    auth_service: Annotated[AuthService, Depends(get_auth_service)],
) -> LogoutResponse:
    """Revoke active token family from Redis, invalidating all sessions in that chain."""
    await auth_service.logout(payload.refresh_token)
    return LogoutResponse(message="Successfully logged out")


@router.get(
    "/me",
    response_model=AuthenticatedUserResponse,
    status_code=status.HTTP_200_OK,
    summary="Stateless token claims inspection without database query",
)
async def get_my_claims(
    current_user: Annotated[AuthenticatedUserResponse, Depends(get_current_authenticated_user)],
) -> AuthenticatedUserResponse:
    """Return claims extracted from the verified access token in O(1) time."""
    return current_user
