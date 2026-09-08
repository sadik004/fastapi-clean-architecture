"""Domain-level exceptions decoupled from HTTP transport layer.

This module establishes a clean, decoupled domain exception hierarchy.
Services and repositories throw domain exceptions, never HTTPExceptions.
Global exception handlers translate these exceptions into standardized HTTP error responses.
"""


class BaseDomainException(Exception):
    """Root base class for all domain-layer exceptions."""

    def __init__(self, message: str, code: str = "DOMAIN_ERROR") -> None:
        self.message = message
        self.code = code
        super().__init__(self.message)


# Backward compatibility alias
DomainException = BaseDomainException


class EntityNotFoundException(BaseDomainException):
    """Raised when a requested domain entity is not found in the persistence layer (HTTP 404)."""

    def __init__(
        self,
        message: str = "Requested entity was not found.",
        code: str = "ENTITY_NOT_FOUND",
    ) -> None:
        super().__init__(message=message, code=code)


class EntityConflictException(BaseDomainException):
    """Raised when an operation conflicts with existing entity state (HTTP 409)."""

    def __init__(
        self,
        message: str = "Entity already exists or conflict occurred.",
        code: str = "ENTITY_CONFLICT",
    ) -> None:
        super().__init__(message=message, code=code)


class AuthorizationException(BaseDomainException):
    """Raised when access is denied due to permission or ownership rules (HTTP 403)."""

    def __init__(
        self,
        message: str = "Access denied: insufficient permissions.",
        code: str = "AUTHORIZATION_FAILED",
    ) -> None:
        super().__init__(message=message, code=code)


class BusinessRuleViolationException(BaseDomainException):
    """Raised when domain invariants or business policies are violated (HTTP 400)."""

    def __init__(
        self,
        message: str = "Business rule violated.",
        code: str = "BUSINESS_RULE_VIOLATION",
    ) -> None:
        super().__init__(message=message, code=code)


class UserAlreadyExistsException(EntityConflictException):
    """Raised when attempting to create a user with duplicate email or username."""

    def __init__(
        self,
        message: str = "A user with this email or username already exists.",
        code: str = "ENTITY_CONFLICT",
    ) -> None:
        super().__init__(message=message, code=code)


class UserNotFoundException(EntityNotFoundException):
    """Raised when a requested user entity does not exist."""

    def __init__(
        self,
        identifier: int | str | None = None,
        *,
        user_id: int | None = None,
        code: str = "ENTITY_NOT_FOUND",
    ) -> None:
        target = user_id if user_id is not None else identifier
        if isinstance(target, int):
            message = f"User with ID {target} was not found."
        elif isinstance(target, str):
            message = f"User with username '{target}' was not found."
        else:
            message = "User was not found."
        super().__init__(message=message, code=code)
