"""User Service containing pure business logic, domain rules, and async task orchestration."""

import asyncio
from datetime import datetime, timezone
import hashlib
from typing import Any, Optional
from app.core.exceptions import UserAlreadyExistsException, UserNotFoundException
from app.core.security import get_password_hash, verify_password
from app.core.unit_of_work import UnitOfWorkProtocol
from app.repositories.post_repository import PostEntity
from app.repositories.user_repository import UserEntity, UserRepositoryProtocol
from app.schemas.user import UserCreate, UserProfileUpdate, UserRole, UserUpdate


class UserService:
    """Service handling User business logic and concurrent task orchestration."""

    def __init__(
        self,
        repository: UserRepositoryProtocol,
        uow: Optional[UnitOfWorkProtocol] = None,
    ) -> None:
        self._repo = repository
        self._uow = uow

    async def register_user(self, payload: UserCreate) -> UserEntity:
        """Register a new user with O(1) uniqueness validation asynchronously.

        Incoming payload fields are already normalized, sanitized, and invariant-verified
        by Pydantic v2 schemas at the application boundary. Transient fields (like
        password_confirm) are safely stripped from persistence operations.

        Raises:
            UserAlreadyExistsException: If email or username is already taken.
        """
        # O(1) duplicate checks using repository inverted hash indexes
        if await self._repo.get_by_email(payload.email) is not None:
            raise UserAlreadyExistsException(f"Email '{payload.email}' is already registered.")

        if await self._repo.get_by_username(payload.username) is not None:
            raise UserAlreadyExistsException(f"Username '{payload.username}' is already taken.")

        # CPU-bound key derivation offloaded to thread pool to prevent event loop starvation
        password_hash = await get_password_hash(payload.password)

        return await self._repo.create(
            email=payload.email,
            username=payload.username,
            password_hash=password_hash,
            age=payload.age,
            role=payload.role.value,
            full_name=payload.full_name,
            phone_number=payload.phone_number,
            bio=payload.bio,
            company_name=payload.company_name,
        )

    async def update_user(self, user_id: int, payload: UserUpdate) -> UserEntity:
        """Update user attributes with O(1) domain uniqueness verification asynchronously.

        Raises:
            UserNotFoundException: If user does not exist.
            UserAlreadyExistsException: If updated email or username is taken by another user.
        """
        user = await self.get_user_by_id(user_id)

        if payload.email is not None and payload.email != user.email:
            existing_email_user = await self._repo.get_by_email(payload.email)
            if existing_email_user is not None and existing_email_user.id != user_id:
                raise UserAlreadyExistsException(
                    f"Email '{payload.email}' is already registered."
                )

        if payload.username is not None and payload.username != user.username:
            existing_user = await self._repo.get_by_username(payload.username)
            if existing_user is not None and existing_user.id != user_id:
                raise UserAlreadyExistsException(
                    f"Username '{payload.username}' is already taken."
                )

        role_val = payload.role.value if payload.role is not None else None

        updated_user = await self._repo.update(
            user_id=user_id,
            email=payload.email,
            username=payload.username,
            age=payload.age,
            role=role_val,
            full_name=payload.full_name,
            phone_number=payload.phone_number,
            bio=payload.bio,
            company_name=payload.company_name,
        )
        if updated_user is None:
            raise UserNotFoundException(user_id=user_id)

        return updated_user

    async def update_profile(self, user_id: int, payload: UserProfileUpdate) -> UserEntity:
        """Update user profile fields (backward compatible helper)."""
        return await self.update_user(
            user_id=user_id,
            payload=UserUpdate(
                username=payload.username,
                age=payload.age,
                full_name=payload.full_name,
                phone_number=payload.phone_number,
                bio=payload.bio,
                company_name=payload.company_name,
            ),
        )

    async def delete_user(self, user_id: int) -> None:
        """Delete a user by primary ID asynchronously.

        Raises:
            UserNotFoundException: If user does not exist.
        """
        deleted = await self._repo.delete(user_id)
        if not deleted:
            raise UserNotFoundException(user_id=user_id)

    async def get_user_by_id(self, user_id: int) -> UserEntity:
        """Fetch user by ID with validation asynchronously.

        Raises:
            UserNotFoundException: If user does not exist.
        """
        user = await self._repo.get_by_id(user_id)
        if user is None:
            raise UserNotFoundException(user_id=user_id)
        return user

    async def get_user_by_username(self, username: str) -> UserEntity:
        """Fetch user by unique username with O(1) hash index lookup asynchronously.

        Raises:
            UserNotFoundException: If user with given username does not exist.
        """
        user = await self._repo.get_by_username(username)
        if user is None:
            raise UserNotFoundException(identifier=username)
        return user

    async def list_users(
        self,
        limit: int = 10,
        offset: int = 0,
        role: Optional[UserRole] = None,
        search: Optional[str] = None,
        is_active: Optional[bool] = None,
    ) -> list[UserEntity]:
        """Fetch registered users with pagination and optional filters asynchronously."""
        role_val = role.value if role else None
        return await self._repo.list_all(
            limit=limit,
            offset=offset,
            role=role_val,
            search=search,
            is_active=is_active,
        )

    async def _fetch_activity_logs(self, user_id: int) -> list[dict[str, Any]]:
        """Simulate non-blocking async network/database I/O to fetch user activity logs."""
        await asyncio.sleep(0.05)
        return [
            {"event": "login", "timestamp": "2026-09-08T10:00:00Z", "ip": "127.0.0.1"},
            {"event": "profile_view", "timestamp": "2026-09-08T10:05:00Z", "user_id": user_id},
        ]

    async def _fetch_account_stats(self, user_id: int) -> dict[str, Any]:
        """Simulate non-blocking async cache/metrics I/O to fetch account statistics."""
        await asyncio.sleep(0.05)
        return {
            "user_id": user_id,
            "total_logins": 42,
            "account_health": "good",
            "storage_used_mb": 128.5,
        }

    async def get_user_dashboard(self, user_id: int) -> dict[str, Any]:
        """Concurrently aggregate profile, activity, and stats using asyncio.gather.

        Executes 3 independent I/O operations concurrently in O(max(t_i)) time instead
        of sequential O(sum(t_i)) time. Ensures clean exception propagation and cancellation
        of sibling tasks if any concurrent branch fails.

        Raises:
            UserNotFoundException: If user profile does not exist.
        """
        tasks: list[asyncio.Task[Any]] = [
            asyncio.create_task(self.get_user_by_id(user_id)),
            asyncio.create_task(self._fetch_activity_logs(user_id)),
            asyncio.create_task(self._fetch_account_stats(user_id)),
        ]

        try:
            results = await asyncio.gather(*tasks)
            profile: UserEntity = results[0]
            activity_logs: list[dict[str, Any]] = results[1]
            stats: dict[str, Any] = results[2]

            return {
                "profile": profile,
                "activity_logs": activity_logs,
                "stats": stats,
            }
        except Exception:
            # Clean exception propagation: cancel any lingering sibling tasks
            for t in tasks:
                if not t.done():
                    t.cancel()
            raise

    async def authenticate_user(
        self,
        username: str,
        password: str,
    ) -> Optional[UserEntity]:
        """Authenticate a user using constant-time hash verification offloaded to worker thread."""
        user = await self._repo.get_by_username(username)
        if user is None:
            return None
        is_valid = await verify_password(password, user.password_hash)
        if not is_valid:
            return None
        return user

    @staticmethod
    def _sync_compute_heavy_report(user_id: int, username: str) -> dict[str, Any]:
        """CPU-bound heavy report generation simulating cryptographic verification over large dataset."""
        hasher = hashlib.sha256()
        records_count = 50_000
        for i in range(records_count):
            hasher.update(f"{user_id}:{username}:{i}".encode("utf-8"))
        return {
            "user_id": user_id,
            "username": username,
            "report_checksum": hasher.hexdigest(),
            "records_processed": records_count,
            "generated_at": datetime.now(timezone.utc),
        }

    async def generate_user_report(self, user_id: int) -> dict[str, Any]:
        """Generate an analytics report by offloading CPU-bound computation to worker thread."""
        user = await self.get_user_by_id(user_id)
        return await asyncio.to_thread(
            self._sync_compute_heavy_report, user.id, user.username
        )

    async def create_user_with_initial_post(
        self,
        user_create: UserCreate,
        post_title: str,
        post_content: str,
        uow: Optional[UnitOfWorkProtocol] = None,
    ) -> tuple[UserEntity, PostEntity]:
        """Atomically register a new user and create their initial post within a Unit of Work.

        Guarantees ACID atomicity: if post creation or commit fails, the user registration
        is completely rolled back, leaving zero orphaned records in the database.
        """
        active_uow = uow or self._uow
        if active_uow is None:
            raise RuntimeError("UnitOfWork is required for atomic multi-entity operations.")

        async with active_uow:
            # Domain uniqueness validation across the shared transaction boundary
            if await active_uow.users.get_by_email(user_create.email) is not None:
                raise UserAlreadyExistsException(f"Email '{user_create.email}' is already registered.")

            if await active_uow.users.get_by_username(user_create.username) is not None:
                raise UserAlreadyExistsException(f"Username '{user_create.username}' is already taken.")

            # CPU-bound password hashing
            password_hash = await get_password_hash(user_create.password)

            # 1. Create user entity via shared transaction
            user = await active_uow.users.create(
                email=user_create.email,
                username=user_create.username,
                password_hash=password_hash,
                age=user_create.age,
                role=user_create.role.value,
                full_name=user_create.full_name,
                phone_number=user_create.phone_number,
                bio=user_create.bio,
                company_name=user_create.company_name,
            )

            # 2. Create initial post referencing the newly flushed user.id
            post = await active_uow.posts.create(
                title=post_title,
                content=post_content,
                user_id=user.id,
            )

            # 3. Atomically commit both repository operations together
            await active_uow.commit()

            return user, post



