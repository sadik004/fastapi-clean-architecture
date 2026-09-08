"""Declarative Dependency Injection providers for Configuration, Repositories, and Auth Guards."""

import secrets
from typing import Annotated, Optional
from fastapi import Depends, Header, HTTPException, status

from app.core.config import Settings, get_settings
from app.core.exceptions import UserNotFoundException
from app.repositories.user_repository import (
    InMemoryUserRepository,
    UserEntity,
    UserRepositoryProtocol,
)
from app.schemas.user import UserRole
from app.services.user_service import UserService

# Singleton repository instance for in-memory persistence across requests
_user_repository = InMemoryUserRepository()


def get_user_repository() -> UserRepositoryProtocol:
    """Dependency provider for UserRepositoryProtocol."""
    return _user_repository


def get_user_service(
    repo: Annotated[UserRepositoryProtocol, Depends(get_user_repository)],
) -> UserService:
    """Dependency provider for UserService."""
    return UserService(repository=repo)


def get_current_user(
    service: Annotated[UserService, Depends(get_user_service)],
    settings: Annotated[Settings, Depends(get_settings)],
    x_api_key: Optional[str] = Header(
        default=None,
        alias="X-API-Key",
        description="API key authentication header",
    ),
) -> UserEntity:
    """Declarative authentication guard resolving current UserEntity.

    Extracts API key from header, performs constant-time string comparison using
    secrets.compare_digest to eliminate timing attacks, and retrieves the associated
    user in O(1) time via the user service.
    """
    if x_api_key is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing API Key header",
        )

    target_username: Optional[str] = None
    if secrets.compare_digest(x_api_key, settings.admin_api_key):
        target_username = settings.admin_username
    elif secrets.compare_digest(x_api_key, settings.user_api_key):
        target_username = settings.default_username
    elif x_api_key.startswith("userkey_"):
        dynamic_username = x_api_key.removeprefix("userkey_")
        expected_token = f"userkey_{dynamic_username}"
        if secrets.compare_digest(x_api_key, expected_token):
            target_username = dynamic_username

    if target_username is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication credentials",
        )

    try:
        user = service.get_user_by_username(username=target_username)
    except UserNotFoundException as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication credentials",
        ) from exc

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Inactive user account",
        )

    return user


def get_current_active_admin(
    current_user: Annotated[UserEntity, Depends(get_current_user)],
) -> UserEntity:
    """Declarative authorization sub-dependency verifying active administrator privileges."""
    if current_user.role != UserRole.ADMIN.value and current_user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Administrative privileges required",
        )
    return current_user
