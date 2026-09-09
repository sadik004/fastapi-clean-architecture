"""User Router handling HTTP endpoints, request/response validation, and status codes."""

from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Path, Query, Response, status

from app.core.dependencies import (
    RoleChecker,
    ScopedTransactionContext,
    TransactionStatus,
    get_analytics_service,
    get_current_active_admin,
    get_current_user,
    get_transaction_context,
    get_uow,
    get_user_repository,
    get_user_service,
    require_permission,
    require_user_ownership,
    track_request_lifecycle,
    transaction_manager,
)
from app.core.exceptions import OptimisticLockException, UserAlreadyExistsException, UserNotFoundException
from app.core.permissions import Permission, grant_permission, revoke_permission
from app.repositories.user_repository import UserEntity
from app.schemas.metrics import UserViewResponse, UserViewsSummaryResponse
from app.schemas.post import (
    PostResponse,
    UserWithInitialPostCreate,
    UserWithInitialPostResponse,
)
from app.schemas.user import (
    UpdateUserNidRequest,
    UpdateUserPermissionsRequest,
    UserAutocompleteResponse,
    UserCreate,
    UserDashboardResponse,
    UserProfileUpdate,
    UserReportResponse,
    UserResponse,
    UserRole,
    UserUpdate,
)
from app.services.analytics_service import AnalyticsService
from app.services.notification_service import record_audit_log, send_welcome_notification
from app.services.user_service import UserService

router = APIRouter(prefix="/users", tags=["Users"])

# Re-export for backwards compatibility with existing test suites
__all__ = [
    "OptimisticLockException",
    "RoleChecker",
    "ScopedTransactionContext",
    "TransactionStatus",
    "UserAlreadyExistsException",
    "UserNotFoundException",
    "get_current_active_admin",
    "get_current_user",
    "get_transaction_context",
    "get_uow",
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
        datetime.now(UTC),
    )
    return UserResponse.model_validate(created_user)


@router.post(
    "/with-initial-post",
    response_model=UserWithInitialPostResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Atomically register a new user and create their initial post",
)
async def create_user_with_initial_post(
    payload: UserWithInitialPostCreate,
    service: Annotated[UserService, Depends(get_user_service)],
) -> UserWithInitialPostResponse:
    """Register a new user and persist an initial post within an atomic Unit of Work transaction."""
    user, post = await service.create_user_with_initial_post(
        user_create=payload.user,
        post_title=payload.post_title,
        post_content=payload.post_content,
    )
    return UserWithInitialPostResponse(
        user=UserResponse.model_validate(user),
        post=PostResponse.model_validate(post),
    )


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
    user = await service.get_user_by_username(username=username)
    return UserResponse.model_validate(user)


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
        1 for u in users if (u.role.value if isinstance(u.role, UserRole) else str(u.role)) == UserRole.ADMIN.value
    )
    return {
        "status": "operational",
        "total_users": len(users),
        "admin_count": admin_count,
    }


@router.get(
    "/autocomplete",
    response_model=list[UserAutocompleteResponse],
    status_code=status.HTTP_200_OK,
    summary="Instant sub-millisecond user autocomplete search via PrefixTrie",
)
async def autocomplete_users(
    q: Annotated[
        str,
        Query(
            min_length=1,
            max_length=50,
            description="Search prefix query string",
        ),
    ],
    limit: Annotated[
        int,
        Query(
            ge=1,
            le=50,
            description="Maximum number of completions to return",
        ),
    ] = 10,
    service: Annotated[UserService, Depends(get_user_service)] = None,  # type: ignore[assignment]
) -> list[UserAutocompleteResponse]:
    """Endpoint for sub-millisecond search-as-you-type user autocomplete.

    Leverages in-memory slotted PrefixTrie achieving O(k) time complexity,
    completely independent of total database records N.
    """
    results = await service.autocomplete_users(prefix=q, limit=limit)
    return [UserAutocompleteResponse.model_validate(item) for item in results]


@router.get(
    "/filter/by-age",
    response_model=list[UserResponse],
    status_code=status.HTTP_200_OK,
    summary="Filter users by age range using O(log N) binary search",
)
async def filter_users_by_age(
    min_age: Annotated[
        int,
        Query(
            ge=0,
            le=150,
            description="Minimum age boundary (inclusive)",
        ),
    ] = 0,
    max_age: Annotated[
        int,
        Query(
            ge=0,
            le=150,
            description="Maximum age boundary (inclusive)",
        ),
    ] = 150,
    service: Annotated[UserService, Depends(get_user_service)] = None,  # type: ignore[assignment]
) -> list[UserResponse]:
    """Filter users within [min_age, max_age] interval via Binary Search range filtering.

    Achieves O(log N + M) time complexity instead of an O(N) full list/table scan.
    """
    if min_age > max_age:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"min_age ({min_age}) cannot be greater than max_age ({max_age}).",
        )
    users = await service.filter_users_by_age(min_age=min_age, max_age=max_age)
    return [UserResponse.model_validate(u) for u in users]


@router.get(
    "/admin/analytics",
    status_code=status.HTTP_200_OK,
    summary="Administrative system analytics",
)
async def get_admin_analytics(
    admin: Annotated[Any, Depends(require_permission(Permission.ADMIN))],
    service: Annotated[UserService, Depends(get_user_service)] = None,  # type: ignore[assignment]
) -> dict[str, Any]:
    """Administrative analytics endpoint guarded strictly by Permission.ADMIN bitmask flag."""
    admin_id = getattr(admin, "user_id", getattr(admin, "id", 0))
    users = await service.list_users(limit=100)
    return {
        "status": "success",
        "admin_id": admin_id,
        "total_active_users": len([u for u in users if u.is_active]),
        "metric": "Bitmasking RBAC Administrative Analytics",
        "timestamp": datetime.now(UTC).isoformat(),
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
    user = await service.get_user_by_id(user_id=user_id)
    return UserResponse.model_validate(user)


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
    dashboard_data = await service.get_user_dashboard(user_id=user_id)
    return UserDashboardResponse(
        profile=UserResponse.model_validate(dashboard_data["profile"]),
        activity_logs=dashboard_data["activity_logs"],
        stats=dashboard_data["stats"],
    )


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
    report = await service.generate_user_report(user_id=user_id)
    return UserReportResponse.model_validate(report)


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
    role: UserRole | None = Query(
        default=None,
        description="Filter users by system role",
    ),
    search: str | None = Query(
        default=None,
        min_length=2,
        max_length=50,
        pattern=r"^[a-zA-Z0-9_ ]+$",
        description="Search term matching username or full name",
    ),
    is_active: bool | None = Query(
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
    updated_user = await service.update_user(user_id=user_id, payload=payload)
    # Safe Memory Boundary: Pass ONLY immutable primitives to background tasks
    background_tasks.add_task(
        record_audit_log,
        "update_user",
        updated_user.id,
        datetime.now(UTC),
    )
    return UserResponse.model_validate(updated_user)


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
    updated_user = await service.update_profile(user_id=user_id, payload=payload)
    return UserResponse.model_validate(updated_user)


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
    current_admin: Annotated[Any, Depends(require_permission(Permission.DELETE))] = None,
    service: Annotated[UserService, Depends(get_user_service)] = None,  # type: ignore[assignment]
    tx: Annotated[ScopedTransactionContext, Depends(get_transaction_context)] = None,  # type: ignore[assignment]
) -> Response:
    """Endpoint to delete a user by ID guarded strictly by Permission.DELETE."""
    if tx is not None:
        tx.stage(f"delete_user:{user_id}")
    await service.delete_user(user_id=user_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.put(
    "/{user_id}/permissions",
    response_model=UserResponse,
    status_code=status.HTTP_200_OK,
    summary="Update user permissions bitmask",
    description="Allows administrators with Permission.ADMIN to grant or revoke specific bitmask flags or set permissions.",
)
async def update_user_permissions(
    payload: UpdateUserPermissionsRequest,
    user_id: int = Path(
        ...,
        ge=1,
        le=2_147_483_647,
        description="The unique positive integer ID of the target user",
    ),
    admin: Annotated[Any, Depends(require_permission(Permission.ADMIN))] = None,
    service: Annotated[UserService, Depends(get_user_service)] = None,  # type: ignore[assignment]
) -> UserResponse:
    """Endpoint allowing an ADMIN to grant, revoke, or set bitmask permission flags on target user."""
    target_user = await service.get_user_by_id(user_id=user_id)
    current_perms = target_user.permissions

    if payload.permissions is not None:
        new_perms = payload.permissions
    else:
        new_perms = current_perms
        if payload.grant is not None:
            new_perms = grant_permission(new_perms, payload.grant)
        if payload.revoke is not None:
            new_perms = revoke_permission(new_perms, payload.revoke)

    updated_user = await service.update_user_permissions(user_id=user_id, permissions=new_perms)
    return UserResponse.model_validate(updated_user)


@router.put(
    "/{user_id}/write-through",
    response_model=UserResponse,
    status_code=status.HTTP_200_OK,
    summary="Update a user using Write-Through caching pattern",
    description="Synchronously persists update to database and simultaneously warms Redis cache with TTL=300.",
)
async def update_user_write_through(
    payload: UserUpdate,
    user_id: int = Path(
        ...,
        ge=1,
        le=2_147_483_647,
        description="The unique positive integer ID of the user",
    ),
    authorized_user: Annotated[UserEntity, Depends(require_user_ownership)] = None,  # type: ignore[assignment]
    service: Annotated[UserService, Depends(get_user_service)] = None,  # type: ignore[assignment]
) -> UserResponse:
    """Endpoint to update user data via the Write-Through caching pattern."""
    updated_user = await service.update_user_write_through(user_id=user_id, payload=payload)
    return UserResponse.model_validate(updated_user)


@router.post(
    "/{user_id}/view",
    response_model=UserViewResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Record a user profile view via Write-Behind caching pattern",
    description="Fast-path in-memory view counter ingestion using Redis HINCRBY without blocking on DB I/O.",
)
async def record_user_view(
    user_id: int = Path(
        ...,
        ge=1,
        le=2_147_483_647,
        description="The unique positive integer ID of the user",
    ),
    analytics_service: Annotated[AnalyticsService, Depends(get_analytics_service)] = None,  # type: ignore[assignment]
) -> UserViewResponse:
    """Endpoint to ingest user view counts asynchronously via Write-Behind pattern."""
    result = await analytics_service.record_view(user_id=user_id)
    return UserViewResponse(**result)


@router.get(
    "/{user_id}/views",
    response_model=UserViewsSummaryResponse,
    status_code=status.HTTP_200_OK,
    summary="Get user profile views summary",
    description="Returns persistent views from repository, pending views in Redis, and combined total views.",
)
async def get_user_views(
    user_id: int = Path(
        ...,
        ge=1,
        le=2_147_483_647,
        description="The unique positive integer ID of the user",
    ),
    analytics_service: Annotated[AnalyticsService, Depends(get_analytics_service)] = None,  # type: ignore[assignment]
) -> UserViewsSummaryResponse:
    """Endpoint to retrieve aggregated view counts across database and write-behind cache."""
    result = await analytics_service.get_user_views(user_id=user_id)
    return UserViewsSummaryResponse(**result)


@router.get(
    "/{user_id}/xfetch",
    response_model=UserResponse,
    status_code=status.HTTP_200_OK,
    summary="Get user profile protected by XFetch Cache Stampede Prevention",
    description="Probabilistic early expiration algorithm prevents Thundering Herd database collapse during key expiration.",
)
async def get_user_by_id_xfetch(
    user_id: int = Path(
        ...,
        ge=1,
        le=2_147_483_647,
        description="The unique positive integer ID of the user",
    ),
    beta: Annotated[float, Query(ge=0.1, le=10.0, description="XFetch aggressiveness factor")] = 1.0,
    service: Annotated[UserService, Depends(get_user_service)] = None,  # type: ignore[assignment]
) -> UserResponse:
    """Fetch user profile with XFetch probabilistic stampede defense."""
    user = await service.get_user_by_id_xfetch(user_id=user_id, beta=beta)
    return UserResponse.model_validate(user)


@router.put(
    "/{user_id}/optimistic",
    response_model=UserResponse,
    status_code=status.HTTP_200_OK,
    summary="Update a user with Optimistic Concurrency Control (OCC) and row versioning",
    description="Atomically updates entity only if expected_version matches database version, preventing lost updates.",
)
async def update_user_optimistic(
    payload: UserUpdate,
    user_id: int = Path(
        ...,
        ge=1,
        le=2_147_483_647,
        description="The unique positive integer ID of the user",
    ),
    expected_version: int = Query(
        ...,
        ge=1,
        description="The version of the entity the client expects to mutate",
    ),
    service: Annotated[UserService, Depends(get_user_service)] = None,  # type: ignore[assignment]
) -> UserResponse:
    """Endpoint to update user attributes using Optimistic Concurrency Control."""
    updated = await service.update_user_optimistic(
        user_id=user_id,
        expected_version=expected_version,
        payload=payload,
    )
    return UserResponse.model_validate(updated)


@router.put(
    "/{user_id}/nid",
    response_model=UserResponse,
    status_code=status.HTTP_200_OK,
    summary="Update sensitive National Identification Number (NID) protected by Field-Level Encryption",
    description="Encrypts NID using Fernet authenticated symmetric cryptography before persisting to database.",
)
async def update_user_nid(
    payload: UpdateUserNidRequest,
    user_id: int = Path(
        ...,
        ge=1,
        le=2_147_483_647,
        description="The unique positive integer ID of the user",
    ),
    service: Annotated[UserService, Depends(get_user_service)] = None,  # type: ignore[assignment]
) -> UserResponse:
    """Endpoint to update encrypted National Identification Number (NID)."""
    updated = await service.update_nid(
        user_id=user_id,
        nid_number=payload.nid_number,
    )
    return UserResponse.model_validate(updated)

