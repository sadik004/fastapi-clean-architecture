"""Domain-level exceptions decoupled from HTTP transport layer."""


class DomainException(Exception):
    """Base domain exception for the application."""

    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(self.message)


class UserAlreadyExistsException(DomainException):
    """Raised when an attempt is made to create an existing user."""

    def __init__(self, message: str = "A user with this email or username already exists.") -> None:
        super().__init__(message)


class UserNotFoundException(DomainException):
    """Raised when a requested user does not exist."""

    def __init__(
        self,
        identifier: int | str | None = None,
        *,
        user_id: int | None = None,
    ) -> None:
        target = user_id if user_id is not None else identifier
        if isinstance(target, int):
            message = f"User with ID {target} was not found."
        elif isinstance(target, str):
            message = f"User with username '{target}' was not found."
        else:
            message = "User was not found."
        super().__init__(message)
