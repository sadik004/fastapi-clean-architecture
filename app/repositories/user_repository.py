"""In-memory User Repository strictly enforcing O(1) hash map operations and Protocol decoupling."""

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional, Protocol


@dataclass
class UserEntity:
    """Internal domain entity stored by the repository."""

    id: int
    email: str
    username: str
    password_hash: str
    is_active: bool
    created_at: datetime
    age: Optional[int] = None
    role: str = "user"
    full_name: Optional[str] = None
    phone_number: Optional[str] = None
    bio: Optional[str] = None
    company_name: Optional[str] = None


class UserRepositoryProtocol(Protocol):
    """Abstract protocol for asynchronous user persistence operations."""

    async def create(
        self,
        email: str,
        username: str,
        password_hash: str,
        age: Optional[int] = None,
        role: str = "user",
        full_name: Optional[str] = None,
        phone_number: Optional[str] = None,
        bio: Optional[str] = None,
        company_name: Optional[str] = None,
    ) -> UserEntity:
        """Create and persist a new user entity asynchronously."""
        ...

    async def get_by_id(self, user_id: int) -> Optional[UserEntity]:
        """Fetch a user by primary key ID asynchronously in O(1) time."""
        ...

    async def get_by_email(self, email: str) -> Optional[UserEntity]:
        """Fetch a user by email via inverted index asynchronously in O(1) time."""
        ...

    async def get_by_username(self, username: str) -> Optional[UserEntity]:
        """Fetch a user by username via inverted index asynchronously in O(1) time."""
        ...

    async def update(
        self,
        user_id: int,
        email: Optional[str] = None,
        username: Optional[str] = None,
        age: Optional[int] = None,
        role: Optional[str] = None,
        full_name: Optional[str] = None,
        phone_number: Optional[str] = None,
        bio: Optional[str] = None,
        company_name: Optional[str] = None,
        update_data: Optional[Any] = None,
    ) -> Optional[UserEntity]:
        """Update an existing user entity and synchronize indexes asynchronously in O(1) time."""
        ...

    async def delete(self, user_id: int) -> bool:
        """Delete an existing user and purge secondary indexes asynchronously in O(1) time."""
        ...

    async def list_all(
        self,
        limit: int = 10,
        offset: int = 0,
        role: Optional[str] = None,
        search: Optional[str] = None,
        is_active: Optional[bool] = None,
    ) -> list[UserEntity]:
        """List user entities asynchronously with pagination and optional filters."""
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

        # Auto-incrementing primary key counter
        self._current_id: int = 0

    async def create(
        self,
        email: str,
        username: str,
        password_hash: str,
        age: Optional[int] = None,
        role: str = "user",
        full_name: Optional[str] = None,
        phone_number: Optional[str] = None,
        bio: Optional[str] = None,
        company_name: Optional[str] = None,
    ) -> UserEntity:
        """Create and store a new user entity with O(1) indexing asynchronously."""
        self._current_id += 1
        new_user = UserEntity(
            id=self._current_id,
            email=email,
            username=username,
            password_hash=password_hash,
            is_active=True,
            created_at=datetime.now(timezone.utc),
            age=age,
            role=role,
            full_name=full_name,
            phone_number=phone_number,
            bio=bio,
            company_name=company_name,
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
        email: Optional[str] = None,
        username: Optional[str] = None,
        age: Optional[int] = None,
        role: Optional[str] = None,
        full_name: Optional[str] = None,
        phone_number: Optional[str] = None,
        bio: Optional[str] = None,
        company_name: Optional[str] = None,
        update_data: Optional[Any] = None,
    ) -> Optional[UserEntity]:
        """Update an existing user entity and synchronize indexes asynchronously in O(1) time."""
        if update_data is not None:
            data = update_data.model_dump(exclude_unset=True) if hasattr(update_data, "model_dump") else dict(update_data)
            email = data.get("email", email)
            username = data.get("username", username)
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

        return user

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

    async def get_by_id(self, user_id: int) -> Optional[UserEntity]:
        """Fetch user by primary key ID asynchronously in O(1) time."""
        return self._store.get(user_id)

    async def get_by_email(self, email: str) -> Optional[UserEntity]:
        """Fetch user by email using hash index asynchronously in O(1) time."""
        user_id = self._email_index.get(email)
        if user_id is None:
            return None
        return self._store.get(user_id)

    async def get_by_username(self, username: str) -> Optional[UserEntity]:
        """Fetch user by username using hash index asynchronously in O(1) time."""
        user_id = self._username_index.get(username)
        if user_id is None:
            return None
        return self._store.get(user_id)

    async def list_all(
        self,
        limit: int = 10,
        offset: int = 0,
        role: Optional[str] = None,
        search: Optional[str] = None,
        is_active: Optional[bool] = None,
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

    def clear(self) -> None:
        """Reset the repository state and purge all secondary indexes."""
        self._store.clear()
        self._email_index.clear()
        self._username_index.clear()
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
]
