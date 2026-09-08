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

    def __init__(self, user_id: int) -> None:
        super().__init__(f"User with ID {user_id} was not found.")
