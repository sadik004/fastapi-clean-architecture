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


class SecurityViolationException(BaseDomainException):
    """Raised when an operation violates security firewalls or sanitization policies (HTTP 400)."""

    def __init__(
        self,
        message: str = "Security policy violation detected.",
        code: str = "SECURITY_VIOLATION",
    ) -> None:
        super().__init__(message=message, code=code)


class SSRFSecurityException(SecurityViolationException):
    """Raised when an outbound URL targets private, loopback, or cloud-metadata IP ranges."""

    def __init__(
        self,
        message: str = "SSRF security violation: Destination address resolves to forbidden IP network.",
        code: str = "SSRF_FORBIDDEN_DESTINATION",
    ) -> None:
        super().__init__(message=message, code=code)


class PathTraversalException(SecurityViolationException):
    """Raised when a filename or path contains directory traversal or injection sequences."""

    def __init__(
        self,
        message: str = "Path traversal violation: Input contains prohibited path manipulation characters.",
        code: str = "PATH_TRAVERSAL_DETECTED",
    ) -> None:
        super().__init__(message=message, code=code)


class EncryptionTamperingException(SecurityViolationException):
    """Raised when ciphertext integrity or HMAC signature check fails during decryption."""

    def __init__(
        self,
        message: str = "Ciphertext integrity verification failed or payload has been tampered with.",
        code: str = "ENCRYPTION_TAMPERING_DETECTED",
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


class OptimisticLockException(EntityConflictException):
    """Raised when an update fails due to a version mismatch in optimistic concurrency control."""

    def __init__(
        self,
        message: str = "Resource was modified by another transaction. Stale version detected; please refresh and retry.",
        code: str = "CONCURRENCY_CONFLICT",
    ) -> None:
        super().__init__(message=message, code=code)


class DistributedLockConflictException(EntityConflictException):
    """Raised when a distributed lock cannot be acquired due to concurrent process execution (HTTP 409)."""

    def __init__(
        self,
        message: str = "Resource is currently locked by another concurrent process. Please retry later.",
        code: str = "LOCK_CONFLICT",
    ) -> None:
        super().__init__(message=message, code=code)


class UserNotFoundException(EntityNotFoundException):
    """Raised when a requested user entity does not exist."""

    def __init__(
        self,
        identifier: int | str | None = None,
        *,
        user_id: int | None = None,
        code: str = "USER_NOT_FOUND",
    ) -> None:
        target = user_id if user_id is not None else identifier
        if isinstance(target, int):
            message = f"User with ID {target} was not found."
        elif isinstance(target, str):
            message = f"User with username '{target}' was not found."
        else:
            message = "User was not found."
        super().__init__(message=message, code=code)


class PlayerNotFoundException(EntityNotFoundException):
    """Raised when a player is not found on a specific leaderboard."""

    def __init__(
        self,
        player_id: str,
        leaderboard_id: str,
        code: str = "PLAYER_NOT_FOUND",
    ) -> None:
        self.player_id = player_id
        self.leaderboard_id = leaderboard_id
        super().__init__(
            message=f"Player '{player_id}' is not present on leaderboard '{leaderboard_id}'.",
            code=code,
        )


class DocumentNotFoundException(EntityNotFoundException):
    """Raised when a requested document entity is not found (HTTP 404)."""

    def __init__(
        self,
        doc_id: int,
        code: str = "DOCUMENT_NOT_FOUND",
    ) -> None:
        self.doc_id = doc_id
        super().__init__(
            message=f"Document with ID {doc_id} was not found.",
            code=code,
        )


class InsufficientStockException(BusinessRuleViolationException):
    """Raised when an inventory deduction request exceeds available stock (HTTP 400)."""

    def __init__(
        self,
        message: str = "Requested quantity exceeds available stock.",
        code: str = "INSUFFICIENT_STOCK",
    ) -> None:
        super().__init__(message=message, code=code)


class ProductNotFoundException(EntityNotFoundException):
    """Raised when a requested product does not exist in inventory (HTTP 404)."""

    def __init__(
        self,
        product_id: int,
        code: str = "ENTITY_NOT_FOUND",
    ) -> None:
        self.product_id = product_id
        super().__init__(
            message=f"Product with ID {product_id} was not found.",
            code=code,
        )


class OrderNotFoundException(EntityNotFoundException):
    """Raised when a requested order does not exist in persistence (HTTP 404)."""

    def __init__(
        self,
        order_id: str,
        code: str = "ORDER_NOT_FOUND",
    ) -> None:
        self.order_id = order_id
        super().__init__(
            message=f"Order with ID '{order_id}' was not found.",
            code=code,
        )
