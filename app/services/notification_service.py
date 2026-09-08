"""Notification and Audit Log Services with Safe Memory Boundaries.

This module provides asynchronous background worker tasks and bounded in-memory event
storage. It guarantees:
1. Safe memory boundaries: Functions accept only immutable primitives or frozen DTOs,
   avoiding request-scoped dependency closure traps.
2. Bounded memory: Stores use collections.deque(maxlen=1000) to guarantee strict O(1)
   space complexity and automatic eviction of stale entries.
3. Resilient execution: Background tasks wrap execution in defensive try...except
   blocks with structured logging so failures never crash the ASGI process.
"""

import asyncio
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
import logging

logger = logging.getLogger(__name__)

# Bounded storage capacity to prevent memory bloat and Out-Of-Memory (OOM) fatal leaks
MAX_AUDIT_ENTRIES: int = 1000
MAX_NOTIFICATION_ENTRIES: int = 1000


@dataclass(frozen=True)
class AuditLogEntry:
    """Immutable audit log event representation."""

    action: str
    user_id: int
    timestamp: datetime


@dataclass(frozen=True)
class NotificationDispatchEntry:
    """Immutable notification dispatch record representation."""

    email: str
    username: str
    timestamp: datetime
    status: str


# Bounded in-memory queues enforcing strict O(1) insertion and eviction
_AUDIT_LOG_STORE: deque[AuditLogEntry] = deque(maxlen=MAX_AUDIT_ENTRIES)
_NOTIFICATION_DISPATCH_STORE: deque[NotificationDispatchEntry] = deque(
    maxlen=MAX_NOTIFICATION_ENTRIES
)


async def send_welcome_notification(email: str, username: str) -> None:
    """Simulate asynchronous external notification dispatch with network latency.

    Strict Invariant: Accepts only immutable primitive strings (email, username) to
    prevent dependency lifetime leaks.

    Resilience: Defensive try...except block with structured logging ensures external
    I/O failures never crash the event loop or terminate the server process.
    """
    try:
        # Simulate external notification service network latency (50ms)
        await asyncio.sleep(0.05)
        entry = NotificationDispatchEntry(
            email=email,
            username=username,
            timestamp=datetime.now(timezone.utc),
            status="dispatched",
        )
        _NOTIFICATION_DISPATCH_STORE.append(entry)
        logger.info(
            "Successfully dispatched welcome notification to %s for username '%s'",
            email,
            username,
        )
    except Exception as exc:
        logger.error(
            "Failed to dispatch welcome notification to %s for username '%s': %s",
            email,
            username,
            exc,
            exc_info=True,
        )


async def record_audit_log(action: str, user_id: int, timestamp: datetime) -> None:
    """Record an audit log entry into the bounded in-memory store.

    Strict Invariant: Accepts only immutable primitives (action, user_id, timestamp).

    Memory Boundary: collections.deque(maxlen=1000) guarantees exact O(1) space bound
    and automatic eviction of oldest entries under continuous load.
    """
    try:
        entry = AuditLogEntry(
            action=action,
            user_id=user_id,
            timestamp=timestamp,
        )
        _AUDIT_LOG_STORE.append(entry)
        logger.info(
            "Recorded audit log: action='%s', user_id=%d, timestamp=%s",
            action,
            user_id,
            timestamp.isoformat(),
        )
    except Exception as exc:
        logger.error(
            "Failed to record audit log action='%s' for user_id=%d: %s",
            action,
            user_id,
            exc,
            exc_info=True,
        )


def get_audit_logs() -> list[AuditLogEntry]:
    """Return an immutable snapshot list of all recorded audit log entries."""
    return list(_AUDIT_LOG_STORE)


def get_notification_logs() -> list[NotificationDispatchEntry]:
    """Return an immutable snapshot list of all dispatched notification entries."""
    return list(_NOTIFICATION_DISPATCH_STORE)


def clear_notification_service() -> None:
    """Clear all in-memory audit logs and notification records for test isolation."""
    _AUDIT_LOG_STORE.clear()
    _NOTIFICATION_DISPATCH_STORE.clear()
