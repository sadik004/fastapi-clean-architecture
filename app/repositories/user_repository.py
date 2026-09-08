"""In-memory User Repository strictly enforcing O(1) hash map operations."""

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional


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


class InMemoryUserRepository:
    """In-memory repository for User persistence.

    Time Complexity:
    - save: O(1) amortized
    - get_by_id: O(1)
    - get_by_email: O(1)
    - get_by_username: O(1)
    - update: O(1)
    - list_all: O(n) where n is total users
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
        username: Optional[str] = None,
        age: Optional[int] = None,
    ) -> Optional[UserEntity]:
        """Update an existing user entity in O(1) time."""
        user = self._store.get(user_id)
        if user is None:
            return None

        if username is not None and username != user.username:
            # Re-index inverted index in O(1)
            self._username_index.pop(user.username, None)
            self._username_index[username] = user.id
            user.username = username

        if age is not None:
            user.age = age

        return user

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

    def list_all(self) -> list[UserEntity]:
        """List all user entities."""
        return list(self._store.values())

    def clear(self) -> None:
        """Reset the repository state (useful for test isolation)."""
        self._store.clear()
        self._email_index.clear()
        self._username_index.clear()
        self._current_id = 0
