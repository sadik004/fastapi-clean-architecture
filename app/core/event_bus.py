"""In-Memory Domain Event Bus & Asynchronous Dispatcher.

Implements the Observer / Publish-Subscribe pattern to decouple core business logic
from auxiliary side-effects. Handlers are executed asynchronously with strict
failure isolation: a failing listener will never crash other listeners or bubble
up to abort the primary domain transaction.
"""

import asyncio
import logging
from collections import defaultdict
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from app.core.events import DomainEvent

logger = logging.getLogger(__name__)

E = TypeVar("E", bound=DomainEvent)
EventHandler = Callable[[Any], Awaitable[None]]


class EventDispatcher:
    """Asynchronous in-memory event bus for decoupled domain event orchestration."""

    def __init__(self) -> None:
        # Map event class -> list of asynchronous handlers
        self._handlers: dict[type[DomainEvent], list[EventHandler]] = defaultdict(list)
        self._lock = asyncio.Lock()
        self._published_count: int = 0
        self._handled_count: int = 0
        self._error_count: int = 0

    def subscribe(self, event_cls: type[E], handler: Callable[[E], Awaitable[None]]) -> None:
        """Register an asynchronous listener for a specific domain event class.

        O(1) insertion into the listener registry.
        """
        if handler not in self._handlers[event_cls]:
            self._handlers[event_cls].append(handler)
            logger.debug(
                "Subscribed handler '%s' to event '%s'",
                getattr(handler, "__name__", str(handler)),
                event_cls.__name__,
            )

    def unsubscribe(self, event_cls: type[E], handler: Callable[[E], Awaitable[None]]) -> None:
        """Deregister an asynchronous listener from an event class."""
        if handler in self._handlers[event_cls]:
            self._handlers[event_cls].remove(handler)
            logger.debug(
                "Unsubscribed handler '%s' from event '%s'",
                getattr(handler, "__name__", str(handler)),
                event_cls.__name__,
            )

    async def _safe_execute(self, handler: EventHandler, event: DomainEvent) -> None:
        """Execute a single event handler with strict exception isolation."""
        handler_name = getattr(handler, "__name__", str(handler))
        try:
            await handler(event)
            self._handled_count += 1
        except Exception as exc:
            self._error_count += 1
            logger.error(
                "Domain event handler '%s' failed for event '%s' [event_id=%s]: %s",
                handler_name,
                event.event_type,
                event.event_id,
                exc,
                exc_info=True,
            )

    async def publish(self, event: DomainEvent) -> None:
        """Dispatch a domain event to all registered listeners asynchronously.

        Guarantees:
        - Handlers are executed concurrently via asyncio.gather.
        - Strict failure isolation: if any listener crashes, others continue unaffected,
          and no exception bubbles up to the caller.
        """
        event_cls = type(event)
        handlers = list(self._handlers.get(event_cls, []))
        self._published_count += 1

        if not handlers:
            logger.debug("No handlers registered for domain event '%s'", event.event_type)
            return

        logger.info(
            "Publishing domain event '%s' [id=%s] to %d listener(s)",
            event.event_type,
            event.event_id,
            len(handlers),
        )

        tasks = [self._safe_execute(h, event) for h in handlers]
        await asyncio.gather(*tasks)

    def clear(self) -> None:
        """Reset all registered handlers and telemetry metrics (primarily for test teardown)."""
        self._handlers.clear()
        self._published_count = 0
        self._handled_count = 0
        self._error_count = 0

    @property
    def published_count(self) -> int:
        """Total number of published domain events."""
        return self._published_count

    @property
    def handled_count(self) -> int:
        """Total number of successfully completed handler executions."""
        return self._handled_count

    @property
    def error_count(self) -> int:
        """Total number of handler execution failures."""
        return self._error_count


# Global singleton event dispatcher
_global_event_dispatcher: EventDispatcher = EventDispatcher()


def get_event_dispatcher() -> EventDispatcher:
    """Return the global singleton instance of EventDispatcher."""
    return _global_event_dispatcher
