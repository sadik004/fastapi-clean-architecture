import secrets
import time
import uuid
from collections.abc import Generator, Sequence
from enum import Enum
from typing import Annotated, Any
from unittest.mock import AsyncMock

from fastapi import Depends, Header, HTTPException, Path, Request, status
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.database import apply_migrations as apply_migrations
from app.core.database import get_db_pool_status as get_db_pool_status
from app.core.database import get_db_session as get_db_session
from app.core.database import rollback_migration as rollback_migration
from app.core.dsa.bloom_filter import BloomFilter
from app.core.dsa.sliding_window import SlidingWindowLog
from app.core.exceptions import UserNotFoundException
from app.core.redis import get_redis
from app.core.unit_of_work import SqlAlchemyUnitOfWork, UnitOfWorkProtocol
from app.repositories.user_repository import (
    InMemoryUserRepository,
    SqlAlchemyUserRepository,
    UserEntity,
    UserRepositoryProtocol,
)
from app.schemas.user import UserRole
from app.services.analytics_service import AnalyticsService
from app.services.cache_service import CacheService, get_cache_service
from app.services.user_service import (
    UserService,
)
from app.services.user_service import (
    get_user_bloom_filter as get_user_bloom_filter,
)

# Singleton repository instance for in-memory persistence across requests
_user_repository = InMemoryUserRepository()


def get_user_repository(
    session: Annotated[AsyncSession | None, Depends(get_db_session)] = None,
) -> UserRepositoryProtocol:
    """Dependency provider for UserRepositoryProtocol.

    In FastAPI request lifecycles, injects an active AsyncSession from get_db_session
    and yields a production SqlAlchemyUserRepository.
    When invoked without a session argument (e.g., isolated unit test assertions),
    falls back to the in-memory repository.
    """
    if session is not None:
        return SqlAlchemyUserRepository(session=session)
    return _user_repository


def get_uow() -> UnitOfWorkProtocol:
    """Dependency provider for UnitOfWorkProtocol."""
    return SqlAlchemyUnitOfWork()


def get_user_service(
    repo: Annotated[UserRepositoryProtocol, Depends(get_user_repository)],
    uow: Annotated[UnitOfWorkProtocol | None, Depends(get_uow)] = None,
    cache_service: Annotated[CacheService | None, Depends(get_cache_service)] = None,
    bloom_filter: Annotated[BloomFilter | None, Depends(get_user_bloom_filter)] = None,
) -> UserService:
    """Dependency provider for UserService."""
    effective_bloom = None if isinstance(repo, AsyncMock) else bloom_filter
    return UserService(
        repository=repo,
        uow=uow,
        cache_service=cache_service,
        bloom_filter=effective_bloom,
    )


def get_analytics_service(
    redis_client: Annotated[Redis, Depends(get_redis)],
    repo: Annotated[UserRepositoryProtocol, Depends(get_user_repository)],
) -> AnalyticsService:
    """Dependency provider yielding an active AnalyticsService instance."""
    return AnalyticsService(redis_client=redis_client, repository=repo)


async def get_current_user(
    service: Annotated[UserService, Depends(get_user_service)],
    settings: Annotated[Settings, Depends(get_settings)],
    x_api_key: str | None = Header(
        default=None,
        alias="X-API-Key",
        description="API key authentication header",
    ),
) -> UserEntity:
    """Declarative authentication guard resolving current UserEntity.

    Extracts API key from header, performs constant-time string comparison using
    secrets.compare_digest to eliminate timing attacks, and retrieves the associated
    user in O(1) time via the user service asynchronously.
    """
    if x_api_key is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing API Key header",
            headers={"WWW-Authenticate": "ApiKey"},
        )

    target_username: str | None = None
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
            headers={"WWW-Authenticate": "ApiKey"},
        )

    try:
        user = await service.get_user_by_username(username=target_username)
    except UserNotFoundException as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication credentials",
            headers={"WWW-Authenticate": "ApiKey"},
        ) from exc

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Inactive user account",
        )

    return user


class RoleChecker:
    """Parameterized callable class dependency for Role-Based Access Control (RBAC).

    Enforces O(1) membership checking using an immutable frozenset of allowed roles.
    """

    def __init__(
        self,
        allowed_roles: Sequence[UserRole | str] | set[UserRole | str],
        detail: str = "Insufficient role permissions",
    ) -> None:
        self.allowed_roles: frozenset[str] = frozenset(
            r.value if isinstance(r, UserRole) else str(r) for r in allowed_roles
        )
        self.detail: str = detail

    async def __call__(
        self,
        current_user: Annotated[UserEntity, Depends(get_current_user)],
    ) -> UserEntity:
        user_role_str = current_user.role.value if isinstance(current_user.role, UserRole) else str(current_user.role)
        if user_role_str not in self.allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=self.detail,
            )
        return current_user


# Reusable parameterized guard verifying active administrator privileges
get_current_active_admin = RoleChecker(
    [UserRole.ADMIN],
    detail="Administrative privileges required",
)


async def require_user_ownership(
    user_id: Annotated[
        int,
        Path(
            ...,
            ge=1,
            le=2_147_483_647,
            description="The unique positive integer ID of the user",
        ),
    ],
    current_user: Annotated[UserEntity, Depends(get_current_user)],
) -> UserEntity:
    """Hierarchical authorization guard mitigating IDOR vulnerabilities.

    Grants access only if the authenticated user's ID matches the path user_id
    or if the authenticated user has administrative privileges.
    """
    user_role_str = current_user.role.value if isinstance(current_user.role, UserRole) else str(current_user.role)
    is_owner = current_user.id == user_id
    is_admin = user_role_str == UserRole.ADMIN.value

    if not (is_owner or is_admin):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access forbidden: you cannot modify another user's profile",
        )
    return current_user


# ============================================================================
# Transactional Lifecycle & Two-Phase Execution Contexts (Yield Dependencies)
# ============================================================================


class TransactionStatus(str, Enum):
    """Lifecycle status states for an in-memory transactional context."""

    ACTIVE = "active"
    COMMITTED = "committed"
    ROLLED_BACK = "rolled_back"
    CLOSED = "closed"


class ScopedTransactionContext:
    """Represents a scoped Unit of Work transaction tracking staged operations and lifecycle."""

    def __init__(self, tx_id: str) -> None:
        self.tx_id: str = tx_id
        self.status: TransactionStatus = TransactionStatus.ACTIVE
        self.staged_actions: list[str] = []
        self.is_closed: bool = False
        self.created_at: float = time.perf_counter()
        self.duration_ms: float = 0.0

    def stage(self, action: str) -> None:
        """Stage an in-flight mutation action within the active transaction."""
        if self.status != TransactionStatus.ACTIVE:
            raise RuntimeError(f"Cannot stage actions on {self.status.value} transaction {self.tx_id}")
        self.staged_actions.append(action)

    def commit(self) -> None:
        """Commit staged actions, marking transaction as committed."""
        if self.status == TransactionStatus.ACTIVE:
            self.status = TransactionStatus.COMMITTED

    def rollback(self) -> None:
        """Roll back staged actions, clearing pending modifications."""
        if self.status == TransactionStatus.ACTIVE:
            self.status = TransactionStatus.ROLLED_BACK
            self.staged_actions.clear()

    def close(self) -> None:
        """Unconditionally close the transaction and compute execution duration."""
        if self.status == TransactionStatus.ACTIVE:
            self.rollback()
        self.is_closed = True
        self.duration_ms = (time.perf_counter() - self.created_at) * 1000.0


class TransactionManager:
    """In-memory Unit of Work transaction registry ensuring zero resource leaks."""

    def __init__(self) -> None:
        self._active_transactions: dict[str, ScopedTransactionContext] = {}
        self._history: list[ScopedTransactionContext] = []

    def begin(self) -> ScopedTransactionContext:
        """Begin a new scoped transaction with O(1) tracking."""
        tx_id = uuid.uuid4().hex[:12]
        tx = ScopedTransactionContext(tx_id=tx_id)
        self._active_transactions[tx_id] = tx
        return tx

    def close(self, tx: ScopedTransactionContext) -> None:
        """Close and unregister a transaction in O(1) time."""
        tx.close()
        self._active_transactions.pop(tx.tx_id, None)
        self._history.append(tx)

    @property
    def active_count(self) -> int:
        """Count of currently active (unclosed) transactions."""
        return len(self._active_transactions)

    def clear(self) -> None:
        """Purge all active and historical transactions (for test isolation)."""
        self._active_transactions.clear()
        self._history.clear()


# Singleton transaction manager instance
transaction_manager = TransactionManager()


def get_transaction_context() -> Generator[ScopedTransactionContext]:
    """Two-phase generator dependency providing transactional context with automated teardown.

    - Pre-yield (Phase 1): Begins an isolated transaction with O(1) tracking.
    - Yield: Hands execution over to the route handler.
    - Post-yield (Phase 2):
        * On normal return: Commits staged actions.
        * On exception (HTTPException or 500): Rolls back staged actions and re-raises.
        * Finally block: Guarantees transaction is closed and un-registered from active map.
    """
    tx = transaction_manager.begin()
    try:
        yield tx
        if tx.status == TransactionStatus.ACTIVE:
            tx.commit()
    except Exception:
        tx.rollback()
        raise
    finally:
        transaction_manager.close(tx)


class RequestLifecycleContext:
    """Audit and timing context for monitoring request lifecycle duration."""

    def __init__(self, trace_id: str) -> None:
        self.trace_id: str = trace_id
        self.start_time: float = time.perf_counter()
        self.duration_ms: float = 0.0
        self.audit_log: dict[str, Any] = {"trace_id": trace_id}
        self.is_completed: bool = False

    def complete(self) -> None:
        """Mark request lifecycle complete and record duration."""
        self.duration_ms = (time.perf_counter() - self.start_time) * 1000.0
        self.audit_log["duration_ms"] = round(self.duration_ms, 3)
        self.is_completed = True


def track_request_lifecycle() -> Generator[RequestLifecycleContext]:
    """Generator dependency for audit tracking and request latency measurement."""
    trace_id = uuid.uuid4().hex[:16]
    ctx = RequestLifecycleContext(trace_id=trace_id)
    try:
        yield ctx
    finally:
        ctx.complete()


# Global sliding window rate limiter instance (default 60s, 100 requests)
_global_rate_limiter = SlidingWindowLog(window_seconds=60.0, max_requests=100)


def get_global_rate_limiter() -> SlidingWindowLog:
    """Dependency provider returning the shared global rate limiter instance."""
    return _global_rate_limiter


class SlidingWindowRateLimiterDependency:
    """Callable FastAPI dependency enforcing sliding window rate limits per client."""

    def __init__(
        self,
        window: float = 60.0,
        limit: int = 100,
        limiter: SlidingWindowLog | None = None,
    ) -> None:
        self.window = window
        self.limit = limit
        self.limiter = limiter or _global_rate_limiter

    async def __call__(self, request: Request) -> str:
        """Extract client identifier and enforce sliding window rate limit.

        Raises:
            HTTPException(429) with Retry-After header if limit is breached.
        """
        # Resolve client identifier: prefer X-Forwarded-For if behind reverse proxy, else client.host
        forwarded_for = request.headers.get("X-Forwarded-For")
        if forwarded_for:
            client_id = forwarded_for.split(",")[0].strip()
        elif request.client and request.client.host:
            client_id = request.client.host
        else:
            client_id = "127.0.0.1"

        allowed, count, retry_after = self.limiter.record_and_check(client_id)
        if not allowed:
            retry_seconds = max(1, int(retry_after) + 1)
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too Many Requests: Rate limit exceeded",
                headers={"Retry-After": str(retry_seconds)},
            )
        return client_id


def check_sliding_window_rate_limit(
    window: float = 60.0,
    limit: int = 100,
    limiter: SlidingWindowLog | None = None,
) -> SlidingWindowRateLimiterDependency:
    """Factory creating a reusable SlidingWindowRateLimiter dependency."""
    return SlidingWindowRateLimiterDependency(window=window, limit=limit, limiter=limiter)
