"""In-memory User Repository strictly enforcing O(1) hash map operations and Protocol decoupling."""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol


@dataclass(slots=True)
class UserEntity:
    """Internal domain entity stored by the repository, memory-optimized with __slots__."""

    id: int
    email: str
    username: str
    password_hash: str
    is_active: bool
    created_at: datetime
    version: int = 1
    age: int | None = None
    role: str = "user"
    full_name: str | None = None
    phone_number: str | None = None
    bio: str | None = None
    company_name: str | None = None
    permissions: int = 3

    @property
    def permission_names(self) -> list[str]:
        """Compute active permission flag names from bitmask."""
        from app.core.permissions import get_permission_names

        return get_permission_names(self.permissions)


@dataclass(slots=True)
class UserWithPostsEntity(UserEntity):
    """Domain entity representing a user along with their eager-loaded posts, memory-optimized with __slots__."""

    posts: list[Any] = field(default_factory=list)


class UserRepositoryProtocol(Protocol):
    """Abstract protocol for asynchronous user persistence operations."""

    async def create(
        self,
        email: str,
        username: str,
        password_hash: str,
        age: int | None = None,
        role: str = "user",
        full_name: str | None = None,
        phone_number: str | None = None,
        bio: str | None = None,
        company_name: str | None = None,
        permissions: int | None = None,
    ) -> UserEntity:
        """Create and persist a new user entity asynchronously."""
        ...

    async def update_permissions(self, user_id: int, permissions: int) -> UserEntity | None:
        """Update permission bitmask flags on a user entity."""
        ...

    async def get_by_id(self, user_id: int) -> UserEntity | None:
        """Fetch a user by primary key ID asynchronously in O(1) time."""
        ...

    async def get_by_email(self, email: str) -> UserEntity | None:
        """Fetch a user by email via inverted index asynchronously in O(1) time."""
        ...

    async def get_by_username(self, username: str) -> UserEntity | None:
        """Fetch a user by username via inverted index asynchronously in O(1) time."""
        ...

    async def update(
        self,
        user_id: int,
        email: str | None = None,
        username: str | None = None,
        age: int | None = None,
        role: str | None = None,
        full_name: str | None = None,
        phone_number: str | None = None,
        bio: str | None = None,
        company_name: str | None = None,
        update_data: Any | None = None,
        password_hash: str | None = None,
    ) -> UserEntity | None:
        """Update an existing user entity and synchronize indexes asynchronously in O(1) time."""
        ...

    async def update_with_optimistic_lock(
        self,
        user_id: int,
        expected_version: int,
        update_data: Any,
    ) -> UserEntity:
        """Atomically update a user entity with optimistic locking and row versioning."""
        ...

    async def delete(self, user_id: int) -> bool:
        """Delete an existing user and purge secondary indexes asynchronously in O(1) time."""
        ...

    async def list_all(
        self,
        limit: int = 10,
        offset: int = 0,
        role: str | None = None,
        search: str | None = None,
        is_active: bool | None = None,
    ) -> list[UserEntity]:
        """List user entities asynchronously with pagination and optional filters."""
        ...

    async def increment_views_batch(self, views_map: dict[int, int]) -> int:
        """Batch increment persistent view counts for multiple users in a single bulk operation."""
        ...

    async def get_views(self, user_id: int) -> int:
        """Fetch persistent view count for a specific user ID."""
        ...

    async def get_all_ids(self) -> list[int]:
        """Fetch all user IDs asynchronously for membership testing and Bloom Filter seeding."""
        ...

    def clear(self) -> None:
        """Reset repository storage and all secondary indexes synchronously."""
        ...


class InMemoryUserRepository:
    """In-memory repository for User persistence with async contracts.

    Time Complexity:
    - create: O(1) amortized
    - get_by_id: O(1)
    - get_by_email: O(1)
    - get_by_username: O(1)
    - update: O(1) amortized
    - delete: O(1) amortized
    - list_all: O(offset + limit) or O(n) filtered
    """

    def __init__(self) -> None:
        # Primary storage: Hash Map indexed by ID for O(1) lookup
        self._store: dict[int, UserEntity] = {}

        # Inverted index Hash Maps for O(1) uniqueness checks
        self._email_index: dict[str, int] = {}
        self._username_index: dict[str, int] = {}

        # Profile view counts storage: user_id -> views
        self._views: dict[int, int] = {}

        # Auto-incrementing primary key counter
        self._current_id: int = 0

    async def create(
        self,
        email: str,
        username: str,
        password_hash: str,
        age: int | None = None,
        role: str = "user",
        full_name: str | None = None,
        phone_number: str | None = None,
        bio: str | None = None,
        company_name: str | None = None,
        permissions: int | None = None,
    ) -> UserEntity:
        """Create and store a new user entity with O(1) indexing asynchronously."""
        self._current_id += 1
        if permissions is None:
            if role == "admin":
                perms = 63
            elif role == "moderator":
                perms = 7
            elif role == "guest":
                perms = 1
            else:
                perms = 3
        else:
            perms = permissions

        new_user = UserEntity(
            id=self._current_id,
            email=email,
            username=username,
            password_hash=password_hash,
            is_active=True,
            created_at=datetime.now(UTC),
            age=age,
            role=role,
            full_name=full_name,
            phone_number=phone_number,
            bio=bio,
            company_name=company_name,
            permissions=perms,
        )

        # O(1) primary storage insert
        self._store[new_user.id] = new_user

        # O(1) secondary inverted index inserts
        self._email_index[email] = new_user.id
        self._username_index[username] = new_user.id

        return new_user

    async def update(
        self,
        user_id: int,
        email: str | None = None,
        username: str | None = None,
        age: int | None = None,
        role: str | None = None,
        full_name: str | None = None,
        phone_number: str | None = None,
        bio: str | None = None,
        company_name: str | None = None,
        update_data: Any | None = None,
        password_hash: str | None = None,
    ) -> UserEntity | None:
        """Update an existing user entity and synchronize indexes asynchronously in O(1) time."""
        if update_data is not None:
            data = (
                update_data.model_dump(exclude_unset=True) if hasattr(update_data, "model_dump") else dict(update_data)
            )
            email = data.get("email", email)
            username = data.get("username", username)
            password_hash = data.get("password_hash", password_hash)
            age = data.get("age", age)
            role_val = data.get("role", role)
            role = (
                role_val.value
                if role_val is not None and hasattr(role_val, "value")
                else (str(role_val) if role_val is not None else role)
            )
            full_name = data.get("full_name", full_name)
            phone_number = data.get("phone_number", phone_number)
            bio = data.get("bio", bio)
            company_name = data.get("company_name", company_name)

        user = self._store.get(user_id)
        if user is None:
            return None

        if email is not None and email != user.email:
            # Synchronize inverted email index in O(1)
            self._email_index.pop(user.email, None)
            self._email_index[email] = user.id
            user.email = email

        if username is not None and username != user.username:
            # Synchronize inverted username index in O(1)
            self._username_index.pop(user.username, None)
            self._username_index[username] = user.id
            user.username = username

        if age is not None:
            user.age = age

        if role is not None:
            user.role = role

        if full_name is not None:
            user.full_name = full_name

        if phone_number is not None:
            user.phone_number = phone_number

        if bio is not None:
            user.bio = bio

        if company_name is not None:
            user.company_name = company_name

        if password_hash is not None:
            user.password_hash = password_hash

        return user

    async def update_with_optimistic_lock(
        self,
        user_id: int,
        expected_version: int,
        update_data: Any,
    ) -> UserEntity:
        """Atomically update a user entity verifying expected version in O(1) time.

        Raises:
            OptimisticLockException: If entity is missing or expected_version does not match current version.
        """
        user = self._store.get(user_id)
        if user is None or user.version != expected_version:
            from app.core.exceptions import OptimisticLockException

            raise OptimisticLockException(
                "Resource was modified by another transaction. Stale version detected; please refresh and retry."
            )

        data = (
            update_data.model_dump(exclude_unset=True)
            if hasattr(update_data, "model_dump")
            else dict(update_data)
        )
        if "email" in data and data["email"] != user.email:
            self._email_index.pop(user.email, None)
            self._email_index[data["email"]] = user.id
            user.email = data["email"]
        if "username" in data and data["username"] != user.username:
            self._username_index.pop(user.username, None)
            self._username_index[data["username"]] = user.id
            user.username = data["username"]
        if "age" in data:
            user.age = data["age"]
        if "role" in data:
            role_val = data["role"]
            user.role = role_val.value if hasattr(role_val, "value") else str(role_val)
        if "full_name" in data:
            user.full_name = data["full_name"]
        if "phone_number" in data:
            user.phone_number = data["phone_number"]
        if "bio" in data:
            user.bio = data["bio"]
        if "company_name" in data:
            user.company_name = data["company_name"]

        user.version += 1
        return user

    async def update_permissions(self, user_id: int, permissions: int) -> UserEntity | None:
        """Update permission bitmask flags for user in O(1) time."""
        user = self._store.get(user_id)
        if user is None:
            return None
        updated_user = UserEntity(
            id=user.id,
            email=user.email,
            username=user.username,
            password_hash=user.password_hash,
            is_active=user.is_active,
            created_at=user.created_at,
            version=user.version + 1,
            age=user.age,
            role=user.role,
            full_name=user.full_name,
            phone_number=user.phone_number,
            bio=user.bio,
            company_name=user.company_name,
            permissions=permissions,
        )
        self._store[user_id] = updated_user
        return updated_user

    async def delete(self, user_id: int) -> bool:
        """Delete an existing user and purge secondary indexes asynchronously in O(1) time."""
        user = self._store.get(user_id)
        if user is None:
            return False

        # Strictly purge secondary inverted indexes to prevent dangling keys
        self._email_index.pop(user.email, None)
        self._username_index.pop(user.username, None)

        # Remove from primary storage
        del self._store[user_id]
        return True

    async def get_by_id(self, user_id: int) -> UserEntity | None:
        """Fetch user by primary key ID asynchronously in O(1) time."""
        return self._store.get(user_id)

    async def get_by_email(self, email: str) -> UserEntity | None:
        """Fetch user by email using hash index asynchronously in O(1) time."""
        user_id = self._email_index.get(email)
        if user_id is None:
            return None
        return self._store.get(user_id)

    async def get_by_username(self, username: str) -> UserEntity | None:
        """Fetch user by username using hash index asynchronously in O(1) time."""
        user_id = self._username_index.get(username)
        if user_id is None:
            return None
        return self._store.get(user_id)

    async def list_all(
        self,
        limit: int = 10,
        offset: int = 0,
        role: str | None = None,
        search: str | None = None,
        is_active: bool | None = None,
    ) -> list[UserEntity]:
        """List user entities asynchronously with O(k) slice pagination and optional single-pass filtering."""
        # Fast-path when no filters are present: directly slice dictionary values in O(offset + limit)
        if role is None and search is None and is_active is None:
            return list(self._store.values())[offset : offset + limit]

        # Single-pass bounded O(n) filter
        search_term = search.lower() if search else None
        filtered: list[UserEntity] = []
        for user in self._store.values():
            if role is not None and user.role != role:
                continue
            if is_active is not None and user.is_active != is_active:
                continue
            if search_term is not None:
                username_match = search_term in user.username.lower()
                email_match = search_term in user.email.lower()
                name_match = user.full_name is not None and search_term in user.full_name.lower()
                if not (username_match or email_match or name_match):
                    continue
            filtered.append(user)

        return filtered[offset : offset + limit]

    async def increment_views_batch(self, views_map: dict[int, int]) -> int:
        """Batch increment persistent view counts for multiple users in a single bulk operation.

        Complexity: O(M) where M is the number of dirty users to update.
        """
        updated_count = 0
        for user_id, count in views_map.items():
            self._views[user_id] = self._views.get(user_id, 0) + count
            updated_count += 1
        return updated_count

    async def get_views(self, user_id: int) -> int:
        """Fetch persistent view count for a specific user ID.

        Complexity: O(1) hash map lookup.
        """
        return self._views.get(user_id, 0)

    async def get_all_ids(self) -> list[int]:
        """Fetch all user IDs asynchronously in O(N) time for Bloom Filter seeding."""
        return list(self._store.keys())

    def clear(self) -> None:
        """Reset the repository state and purge all secondary indexes."""
        self._store.clear()
        self._email_index.clear()
        self._username_index.clear()
        self._views.clear()
        self._current_id = 0


# Re-export SqlAlchemyUserRepository for clean module namespace
from app.repositories.sqlalchemy_user_repository import (  # noqa: E402
    SqlAlchemyUserRepository as SqlAlchemyUserRepository,
)

__all__ = [
    "InMemoryUserRepository",
    "SqlAlchemyUserRepository",
    "UserEntity",
    "UserRepositoryProtocol",
    "UserWithPostsEntity",
]
