"""User Service containing pure business logic and domain rules."""

from app.core.exceptions import UserAlreadyExistsException, UserNotFoundException
from app.repositories.user_repository import UserEntity, UserRepositoryProtocol
from app.schemas.user import UserCreate, UserProfileUpdate, UserUpdate


class UserService:
    """Service handling User business logic and domain validation."""

    def __init__(self, repository: UserRepositoryProtocol) -> None:
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

    def update_user(self, user_id: int, payload: UserUpdate) -> UserEntity:
        """Update user attributes with O(1) domain uniqueness verification.

        Raises:
            UserNotFoundException: If user does not exist.
            UserAlreadyExistsException: If updated email or username is taken by another user.
        """
        user = self.get_user_by_id(user_id)

        if payload.email is not None and payload.email != user.email:
            existing_email_user = self._repo.get_by_email(payload.email)
            if existing_email_user is not None and existing_email_user.id != user_id:
                raise UserAlreadyExistsException(
                    f"Email '{payload.email}' is already registered."
                )

        if payload.username is not None and payload.username != user.username:
            existing_user = self._repo.get_by_username(payload.username)
            if existing_user is not None and existing_user.id != user_id:
                raise UserAlreadyExistsException(
                    f"Username '{payload.username}' is already taken."
                )

        role_val = payload.role.value if payload.role is not None else None

        updated_user = self._repo.update(
            user_id=user_id,
            email=payload.email,
            username=payload.username,
            age=payload.age,
            role=role_val,
        )
        if updated_user is None:
            raise UserNotFoundException(user_id=user_id)

        return updated_user

    def update_profile(self, user_id: int, payload: UserProfileUpdate) -> UserEntity:
        """Update user profile fields (backward compatible helper)."""
        return self.update_user(
            user_id=user_id,
            payload=UserUpdate(username=payload.username, age=payload.age),
        )

    def delete_user(self, user_id: int) -> None:
        """Delete a user by primary ID.

        Raises:
            UserNotFoundException: If user does not exist.
        """
        deleted = self._repo.delete(user_id)
        if not deleted:
            raise UserNotFoundException(user_id=user_id)

    def get_user_by_id(self, user_id: int) -> UserEntity:
        """Fetch user by ID with validation.

        Raises:
            UserNotFoundException: If user does not exist.
        """
        user = self._repo.get_by_id(user_id)
        if user is None:
            raise UserNotFoundException(user_id=user_id)
        return user

    def list_users(self, limit: int = 10, offset: int = 0) -> list[UserEntity]:
        """Fetch registered users with pagination."""
        return self._repo.list_all(limit=limit, offset=offset)
