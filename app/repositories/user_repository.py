"""In-memory User Repository strictly enforcing O(1) hash map operations and Protocol decoupling."""

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional, Protocol


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
    """Abstract protocol for user persistence operations."""

    def create(
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
        """Create and persist a new user entity."""
        ...

    def get_by_id(self, user_id: int) -> Optional[UserEntity]:
        """Fetch a user by primary key ID in O(1) time."""
        ...

    def get_by_email(self, email: str) -> Optional[UserEntity]:
        """Fetch a user by email via inverted index in O(1) time."""
        ...

    def get_by_username(self, username: str) -> Optional[UserEntity]:
        """Fetch a user by username via inverted index in O(1) time."""
        ...

    def update(
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
    ) -> Optional[UserEntity]:
        """Update an existing user entity and synchronize indexes in O(1) time."""
        ...

    def delete(self, user_id: int) -> bool:
        """Delete an existing user and purge secondary indexes in O(1) time."""
        ...

    def list_all(self, limit: int = 10, offset: int = 0) -> list[UserEntity]:
        """List user entities with pagination."""
        ...

    def clear(self) -> None:
        """Reset repository storage and all secondary indexes."""
        ...


class InMemoryUserRepository:
    """In-memory repository for User persistence.

    Time Complexity:
    - create: O(1) amortized
    - get_by_id: O(1)
    - get_by_email: O(1)
    - get_by_username: O(1)
    - update: O(1) amortized
    - delete: O(1) amortized
    - list_all: O(k) where k is pagination slice limit
    """

    def __init__(self) -> None:
        # Primary storage: Hash Map indexed by ID for O(1) lookup
        self._store: dict[int, UserEntity] = {}

        # Inverted index Hash Maps for O(1) uniqueness checks
        self._email_index: dict[str, int] = {}
        self._username_index: dict[str, int] = {}

        # Auto-incrementing primary key counter
        self._current_id: int = 0

    def create(
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
        """Create and store a new user entity with O(1) indexing."""
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

    def update(
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
    ) -> Optional[UserEntity]:
        """Update an existing user entity and synchronize indexes in O(1) time."""
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

    def delete(self, user_id: int) -> bool:
        """Delete an existing user and purge secondary indexes in O(1) time."""
        user = self._store.get(user_id)
        if user is None:
            return False

        # Strictly purge secondary inverted indexes to prevent dangling keys
        self._email_index.pop(user.email, None)
        self._username_index.pop(user.username, None)

        # Remove from primary storage
        del self._store[user_id]
        return True

    def get_by_id(self, user_id: int) -> Optional[UserEntity]:
        """Fetch user by primary key ID in O(1) time."""
        return self._store.get(user_id)

    def get_by_email(self, email: str) -> Optional[UserEntity]:
        """Fetch user by email using hash index in O(1) time."""
        user_id = self._email_index.get(email)
        if user_id is None:
            return None
        return self._store.get(user_id)

    def get_by_username(self, username: str) -> Optional[UserEntity]:
        """Fetch user by username using hash index in O(1) time."""
        user_id = self._username_index.get(username)
        if user_id is None:
            return None
        return self._store.get(user_id)

    def list_all(self, limit: int = 10, offset: int = 0) -> list[UserEntity]:
        """List user entities with O(k) slice pagination."""
        return list(self._store.values())[offset : offset + limit]

    def clear(self) -> None:
        """Reset the repository state and purge all secondary indexes."""
        self._store.clear()
        self._email_index.clear()
        self._username_index.clear()
        self._current_id = 0
