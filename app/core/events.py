"""Domain Event Data Contracts and Immutability Envelopes.

Following Domain-Driven Design (DDD), domain events represent immutable facts that
occurred in the domain. They are implemented as frozen, slotted dataclasses to ensure
thread safety, structural immutability, and zero post-emission mutation.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import uuid4


@dataclass(slots=True, frozen=True, kw_only=True)
class DomainEvent:
    """Base contract for all domain events across the system."""

    event_id: str = field(default_factory=lambda: str(uuid4()))
    occurred_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    event_type: str = field(default="")

    def __post_init__(self) -> None:
        """Assign concrete class name to event_type if omitted."""
        if not self.event_type:
            object.__setattr__(self, "event_type", self.__class__.__name__)


@dataclass(slots=True, frozen=True, kw_only=True)
class UserRegisteredEvent(DomainEvent):
    """Event emitted when a new user is successfully registered and persisted."""

    user_id: int
    email: str
    username: str


@dataclass(slots=True, frozen=True, kw_only=True)
class OrderPlacedEvent(DomainEvent):
    """Event emitted when a customer successfully places an order."""

    order_id: str
    user_id: int
    total_amount: float


@dataclass(slots=True, frozen=True, kw_only=True)
class ProductStockDepletedEvent(DomainEvent):
    """Event emitted when inventory stock for a product reaches zero or critical threshold."""

    product_id: int
    remaining_stock: int
