"""Decoupled Domain Event Handlers.

These handlers represent auxiliary side-effects triggered in response to domain events.
They are completely decoupled from core domain services (such as UserService),
meaning changes or failures in these side-effects do not affect core transactions.
"""

import logging
from typing import Any

from app.core.event_bus import EventDispatcher, get_event_dispatcher
from app.core.events import OrderPlacedEvent, UserRegisteredEvent

logger = logging.getLogger(__name__)

# In-memory sinks for inspection, telemetry, and test assertions
_recorded_welcome_emails: list[dict[str, Any]] = []
_recorded_audit_logs: list[dict[str, Any]] = []
_recorded_order_analytics: list[dict[str, Any]] = []


async def send_welcome_email_on_registration(event: UserRegisteredEvent) -> None:
    """Simulate sending a welcome onboarding email upon user registration."""
    logger.info(
        "[Side-Effect: Email] Dispatching welcome email to %s (user_id=%d, username=%s) [event_id=%s]",
        event.email,
        event.user_id,
        event.username,
        event.event_id,
    )
    _recorded_welcome_emails.append(
        {
            "event_id": event.event_id,
            "user_id": event.user_id,
            "email": event.email,
            "username": event.username,
            "timestamp": event.occurred_at.isoformat(),
        }
    )


async def record_audit_log_on_registration(event: UserRegisteredEvent) -> None:
    """Record an immutable security audit entry when a new user registers."""
    logger.info(
        "[Side-Effect: Audit] Recording audit log for new user: %s [event_id=%s]",
        event.username,
        event.event_id,
    )
    _recorded_audit_logs.append(
        {
            "event_id": event.event_id,
            "action": "USER_REGISTERED",
            "actor_id": event.user_id,
            "actor_username": event.username,
            "timestamp": event.occurred_at.isoformat(),
        }
    )


async def track_analytics_on_order(event: OrderPlacedEvent) -> None:
    """Track conversion and revenue analytics when an order is placed."""
    logger.info(
        "[Side-Effect: Analytics] Tracking order placed: %s for user %d, amount $%.2f [event_id=%s]",
        event.order_id,
        event.user_id,
        event.total_amount,
        event.event_id,
    )
    _recorded_order_analytics.append(
        {
            "event_id": event.event_id,
            "order_id": event.order_id,
            "user_id": event.user_id,
            "total_amount": event.total_amount,
            "timestamp": event.occurred_at.isoformat(),
        }
    )


def register_default_event_handlers(dispatcher: EventDispatcher | None = None) -> None:
    """Register all default domain event handlers to the provided or global dispatcher."""
    bus = dispatcher if dispatcher is not None else get_event_dispatcher()
    bus.subscribe(UserRegisteredEvent, send_welcome_email_on_registration)
    bus.subscribe(UserRegisteredEvent, record_audit_log_on_registration)
    bus.subscribe(OrderPlacedEvent, track_analytics_on_order)
    logger.info("Default domain event handlers registered successfully.")


def get_recorded_welcome_emails() -> list[dict[str, Any]]:
    """Return all recorded welcome email dispatches."""
    return list(_recorded_welcome_emails)


def get_recorded_audit_logs() -> list[dict[str, Any]]:
    """Return all recorded registration audit logs."""
    return list(_recorded_audit_logs)


def get_recorded_order_analytics() -> list[dict[str, Any]]:
    """Return all recorded order analytics events."""
    return list(_recorded_order_analytics)


def clear_recorded_events() -> None:
    """Clear all recorded event histories (for test isolation)."""
    _recorded_welcome_emails.clear()
    _recorded_audit_logs.clear()
    _recorded_order_analytics.clear()
