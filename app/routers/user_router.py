"""User Router handling HTTP endpoints, request/response validation, and status codes."""

from datetime import datetime, timezone
from typing import Annotated, Optional
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Path, Query, Response, status

from app.core.dependencies import (
    RoleChecker,
    ScopedTransactionContext,
    TransactionStatus,
    get_current_active_admin,
    get_current_user,
    get_transaction_context,
    get_user_repository,
    get_user_service,
    require_user_ownership,
    track_request_lifecycle,
    transaction_manager,
)
from app.core.exceptions import UserAlreadyExistsException, UserNotFoundException
from app.repositories.user_repository import UserEntity
from app.schemas.user import (
    UserCreate,
    UserDashboardResponse,
    UserProfileUpdate,
    UserReportResponse,
    UserResponse,
    UserRole,
    UserUpdate,
)
from app.services.notification_service import record_audit_log, send_welcome_notification
from app.services.user_service import UserService

router = APIRouter(prefix="/users", tags=["Users"])

# Re-export for backwards compatibility with existing test suites
__all__ = [
    "RoleChecker",
    "ScopedTransactionContext",
    "TransactionStatus",
    "get_current_active_admin",
    "get_current_user",
    "get_transaction_context",
    "get_user_repository",
    "get_user_service",
    "require_user_ownership",
    "router",
    "track_request_lifecycle",
    "transaction_manager",
]


@router.post(
    "/",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register a new user",
)
async def create_user(
    payload: UserCreate,
    service: Annotated[UserService, Depends(get_user_service)],
    background_tasks: BackgroundTasks = BackgroundTasks(),
    tx: Annotated[ScopedTransactionContext, Depends(get_transaction_context)] = None,  # type: ignore[assignment]
) -> UserResponse:
    """Endpoint to register a new user."""
    if tx is not None:
        tx.stage(f"create_user:{payload.username}")
    try:
        created_user = await service.register_user(payload=payload)
        # Safe Memory Boundary: Pass ONLY immutable primitives, NEVER request-scoped dependencies
        background_tasks.add_task(
            send_welcome_notification,
            created_user.email,
            created_user.username,
        )
        background_tasks.add_task(
            record_audit_log,
            "create_user",
            created_user.id,
            datetime.now(timezone.utc),
        )
        return UserResponse.model_validate(created_user)
    except UserAlreadyExistsException as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=exc.message,
        ) from exc


@router.get(
    "/by-username/{username}",
    response_model=UserResponse,
    status_code=status.HTTP_200_OK,
    summary="Get user by username",
)
async def get_user_by_username(
    username: str = Path(
        ...,
        min_length=3,
        max_length=50,
        pattern=r"^[a-z0-9_]+$",
        description="Normalized lowercase alphanumeric username",
    ),
    service: Annotated[UserService, Depends(get_user_service)] = None,  # type: ignore[assignment]
) -> UserResponse:
    """Endpoint to fetch a user by normalized username."""
    try:
        user = await service.get_user_by_username(username=username)
        return UserResponse.model_validate(user)
    except UserNotFoundException as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=exc.message,
        ) from exc


@router.get(
    "/me",
    response_model=UserResponse,
    status_code=status.HTTP_200_OK,
    summary="Get currently authenticated user profile",
)
async def get_current_user_profile(
    current_user: Annotated[UserEntity, Depends(get_current_user)],
) -> UserResponse:
    """Endpoint to fetch the authenticated user profile."""
    return UserResponse.model_validate(current_user)


@router.get(
    "/admin/metrics",
    status_code=status.HTTP_200_OK,
    summary="Get administrative system metrics",
)
async def get_admin_metrics(
    current_admin: Annotated[UserEntity, Depends(RoleChecker([UserRole.ADMIN]))],
    service: Annotated[UserService, Depends(get_user_service)],
) -> dict[str, int | str]:
    """Protected endpoint for administrators to view system user metrics."""
    users = await service.list_users(limit=100, offset=0)
    admin_count = sum(
        1
        for u in users
        if (u.role.value if isinstance(u.role, UserRole) else str(u.role))
        == UserRole.ADMIN.value
    )
    return {
        "status": "operational",
        "total_users": len(users),
        "admin_count": admin_count,
    }


@router.get(
    "/{user_id}",
    response_model=UserResponse,
    status_code=status.HTTP_200_OK,
    summary="Get user by ID",
)
async def get_user_by_id(
    user_id: int = Path(
        ...,
        ge=1,
        le=2_147_483_647,
        description="The unique positive integer ID of the user",
    ),
    service: Annotated[UserService, Depends(get_user_service)] = None,  # type: ignore[assignment]
) -> UserResponse:
    """Endpoint to fetch a user by ID."""
    try:
        user = await service.get_user_by_id(user_id=user_id)
        return UserResponse.model_validate(user)
    except UserNotFoundException as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=exc.message,
        ) from exc


@router.get(
    "/{user_id}/dashboard",
    response_model=UserDashboardResponse,
    status_code=status.HTTP_200_OK,
    summary="Get user dashboard with concurrent data aggregation",
)
async def get_user_dashboard(
    user_id: int = Path(
        ...,
        ge=1,
        le=2_147_483_647,
        description="The unique positive integer ID of the user",
    ),
    service: Annotated[UserService, Depends(get_user_service)] = None,  # type: ignore[assignment]
) -> UserDashboardResponse:
    """Endpoint to aggregate user profile, activity logs, and account metrics concurrently."""
    try:
        dashboard_data = await service.get_user_dashboard(user_id=user_id)
        return UserDashboardResponse(
            profile=UserResponse.model_validate(dashboard_data["profile"]),
            activity_logs=dashboard_data["activity_logs"],
            stats=dashboard_data["stats"],
        )
    except UserNotFoundException as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=exc.message,
        ) from exc


@router.post(
    "/{user_id}/export-report",
    response_model=UserReportResponse,
    status_code=status.HTTP_200_OK,
    summary="Generate CPU-bound analytical user report offloaded to worker thread",
)
async def export_user_report(
    user_id: int = Path(
        ...,
        ge=1,
        le=2_147_483_647,
        description="The unique positive integer ID of the user",
    ),
    service: Annotated[UserService, Depends(get_user_service)] = None,  # type: ignore[assignment]
) -> UserReportResponse:
    """Endpoint triggering heavy report calculation offloaded via asyncio.to_thread."""
    try:
        report = await service.generate_user_report(user_id=user_id)
        return UserReportResponse.model_validate(report)
    except UserNotFoundException as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=exc.message,
        ) from exc



@router.get(
    "/",
    response_model=list[UserResponse],
    status_code=status.HTTP_200_OK,
    summary="List all users with pagination and filtering",
)
async def list_users(
    service: Annotated[UserService, Depends(get_user_service)],
    limit: int = Query(
        default=10,
        ge=1,
        le=100,
        description="Max users to return (1-100)",
    ),
    offset: int = Query(
        default=0,
        ge=0,
        description="Zero-based pagination offset",
    ),
    role: Optional[UserRole] = Query(
        default=None,
        description="Filter users by system role",
    ),
    search: Optional[str] = Query(
        default=None,
        min_length=2,
        max_length=50,
        pattern=r"^[a-zA-Z0-9_ ]+$",
        description="Search term matching username or full name",
    ),
    is_active: Optional[bool] = Query(
        default=None,
        description="Filter users by active status",
    ),
) -> list[UserResponse]:
    """Endpoint to retrieve users with limit-offset pagination and filtering."""
    users = await service.list_users(
        limit=limit,
        offset=offset,
        role=role,
        search=search,
        is_active=is_active,
    )
    return [UserResponse.model_validate(u) for u in users]


@router.put(
    "/{user_id}",
    response_model=UserResponse,
    status_code=status.HTTP_200_OK,
    summary="Update an existing user",
)
async def update_user(
    payload: UserUpdate,
    user_id: int = Path(
        ...,
        ge=1,
        le=2_147_483_647,
        description="The unique positive integer ID of the user",
    ),
    authorized_user: Annotated[UserEntity, Depends(require_user_ownership)] = None,  # type: ignore[assignment]
    service: Annotated[UserService, Depends(get_user_service)] = None,  # type: ignore[assignment]
    background_tasks: BackgroundTasks = BackgroundTasks(),
    tx: Annotated[ScopedTransactionContext, Depends(get_transaction_context)] = None,  # type: ignore[assignment]
) -> UserResponse:
    """Endpoint to update user attributes."""
    if tx is not None:
        tx.stage(f"update_user:{user_id}")
    try:
        updated_user = await service.update_user(user_id=user_id, payload=payload)
        # Safe Memory Boundary: Pass ONLY immutable primitives to background tasks
        background_tasks.add_task(
            record_audit_log,
            "update_user",
            updated_user.id,
            datetime.now(timezone.utc),
        )
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


@router.patch(
    "/{user_id}",
    response_model=UserResponse,
    status_code=status.HTTP_200_OK,
    summary="Update a user profile",
)
async def update_user_profile(
    payload: UserProfileUpdate,
    user_id: int = Path(
        ...,
        ge=1,
        le=2_147_483_647,
        description="The unique positive integer ID of the user",
    ),
    authorized_user: Annotated[UserEntity, Depends(require_user_ownership)] = None,  # type: ignore[assignment]
    service: Annotated[UserService, Depends(get_user_service)] = None,  # type: ignore[assignment]
    tx: Annotated[ScopedTransactionContext, Depends(get_transaction_context)] = None,  # type: ignore[assignment]
) -> UserResponse:
    """Endpoint to partially update user profile attributes."""
    if tx is not None:
        tx.stage(f"patch_user:{user_id}")
    try:
        updated_user = await service.update_profile(user_id=user_id, payload=payload)
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


@router.delete(
    "/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a user",
)
async def delete_user(
    user_id: int = Path(
        ...,
        ge=1,
        le=2_147_483_647,
        description="The unique positive integer ID of the user",
    ),
    current_admin: Annotated[UserEntity, Depends(get_current_active_admin)] = None,  # type: ignore[assignment]
    service: Annotated[UserService, Depends(get_user_service)] = None,  # type: ignore[assignment]
    tx: Annotated[ScopedTransactionContext, Depends(get_transaction_context)] = None,  # type: ignore[assignment]
) -> Response:
    """Endpoint to delete a user by ID."""
    if tx is not None:
        tx.stage(f"delete_user:{user_id}")
    try:
        await service.delete_user(user_id=user_id)
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except UserNotFoundException as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=exc.message,
        ) from exc

