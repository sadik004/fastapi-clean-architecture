"""User Service containing pure business logic and domain rules."""

from app.core.exceptions import UserAlreadyExistsException, UserNotFoundException
from app.repositories.user_repository import InMemoryUserRepository, UserEntity
from app.schemas.user import UserCreate, UserProfileUpdate


class UserService:
    """Service handling User business logic and domain validation."""

    def __init__(self, repository: InMemoryUserRepository) -> None:
        self._repo = repository

    def register_user(self, payload: UserCreate) -> UserEntity:
        """Register a new user with O(1) uniqueness validation.

        Raises:
            UserAlreadyExistsException: If email or username is already taken.
        """
        # O(1) duplicate checks using repository inverted hash indexes
        if self._repo.get_by_email(payload.email) is not None:
            raise UserAlreadyExistsException(f"Email '{payload.email}' is already registered.")

        if self._repo.get_by_username(payload.username) is not None:
            raise UserAlreadyExistsException(f"Username '{payload.username}' is already taken.")

        # Simulate secure hash generation before persistence
        password_hash = f"argon2_hash_{payload.password}"

        return self._repo.create(
            email=payload.email,
            username=payload.username,
            password_hash=password_hash,
            age=payload.age,
            role=payload.role.value,
        )

    def update_profile(self, user_id: int, payload: UserProfileUpdate) -> UserEntity:
        """Update a user profile with O(1) domain validation.

        Raises:
            UserNotFoundException: If user does not exist.
            UserAlreadyExistsException: If updated username is already taken.
        """
        user = self.get_user_by_id(user_id)

        if payload.username is not None and payload.username != user.username:
            existing_user = self._repo.get_by_username(payload.username)
            if existing_user is not None and existing_user.id != user_id:
                raise UserAlreadyExistsException(
                    f"Username '{payload.username}' is already taken."
                )

        updated_user = self._repo.update(
            user_id=user_id,
            username=payload.username,
            age=payload.age,
        )
        if updated_user is None:
            raise UserNotFoundException(user_id=user_id)

        return updated_user

    def get_user_by_id(self, user_id: int) -> UserEntity:
        """Fetch user by ID with validation.

        Raises:
            UserNotFoundException: If user does not exist.
        """
        user = self._repo.get_by_id(user_id)
        if user is None:
            raise UserNotFoundException(user_id=user_id)
        return user

    def get_all_users(self) -> list[UserEntity]:
        """Fetch all registered users."""
        return self._repo.list_all()
