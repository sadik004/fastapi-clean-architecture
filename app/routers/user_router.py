"""User Router handling HTTP endpoints, request/response validation, and status codes."""

from typing import Annotated
from fastapi import APIRouter, Depends, HTTPException, status

from app.core.exceptions import UserAlreadyExistsException, UserNotFoundException
from app.repositories.user_repository import InMemoryUserRepository
from app.schemas.user import UserCreate, UserProfileUpdate, UserResponse
from app.services.user_service import UserService

router = APIRouter(prefix="/users", tags=["Users"])

# Singleton repository instance for in-memory persistence across requests
_user_repository = InMemoryUserRepository()


def get_user_repository() -> InMemoryUserRepository:
    """Dependency provider for InMemoryUserRepository."""
    return _user_repository


def get_user_service(
    repo: Annotated[InMemoryUserRepository, Depends(get_user_repository)],
) -> UserService:
    """Dependency provider for UserService."""
    return UserService(repository=repo)


@router.post(
    "/",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register a new user",
)
def create_user(
    payload: UserCreate,
    service: Annotated[UserService, Depends(get_user_service)],
) -> UserResponse:
    """Endpoint to register a new user."""
    try:
        created_user = service.register_user(payload=payload)
        return UserResponse.model_validate(created_user)
    except UserAlreadyExistsException as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=exc.message,
        ) from exc


@router.patch(
    "/{user_id}",
    response_model=UserResponse,
    status_code=status.HTTP_200_OK,
    summary="Update a user profile",
)
def update_user_profile(
    user_id: int,
    payload: UserProfileUpdate,
    service: Annotated[UserService, Depends(get_user_service)],
) -> UserResponse:
    """Endpoint to partially update user profile attributes."""
    try:
        updated_user = service.update_profile(user_id=user_id, payload=payload)
        return UserResponse.model_validate(updated_user)
    except UserNotFoundException as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=exc.message,
        ) from exc
    except UserAlreadyExistsException as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=exc.message,
        ) from exc


@router.get(
    "/{user_id}",
    response_model=UserResponse,
    status_code=status.HTTP_200_OK,
    summary="Get user by ID",
)
def get_user_by_id(
    user_id: int,
    service: Annotated[UserService, Depends(get_user_service)],
) -> UserResponse:
    """Endpoint to fetch a user by ID."""
    try:
        user = service.get_user_by_id(user_id=user_id)
        return UserResponse.model_validate(user)
    except UserNotFoundException as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=exc.message,
        ) from exc


@router.get(
    "/",
    response_model=list[UserResponse],
    status_code=status.HTTP_200_OK,
    summary="List all users",
)
def list_users(
    service: Annotated[UserService, Depends(get_user_service)],
) -> list[UserResponse]:
    """Endpoint to retrieve all users."""
    users = service.get_all_users()
    return [UserResponse.model_validate(u) for u in users]
