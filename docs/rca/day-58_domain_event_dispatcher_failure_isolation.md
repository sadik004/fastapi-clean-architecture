# RCA: Domain Events Architecture & Listener Exception Isolation

- **Milestone**: Day 58
- **Topic**: Domain Events & Decoupled Architecture (Pure Domain Event Dispatcher & In-Memory Event Bus)
- **Trigger**: Architectural Review & Concurrency Error Boundary Verification

---

## Faulty Code / Anti-Pattern

```python
# Anti-pattern 1: Mutable domain events
@dataclass
class OrderCreatedEvent:
    order_id: str
    amount: float

# Anti-pattern 2: Direct sequential listener execution without exception isolation
class EventDispatcher:
    async def dispatch(self, event: DomainEvent) -> None:
        for handler in self._handlers.get(type(event), []):
            # If handler fails (e.g. SMTP timeout), it crashes the caller's transaction
            # and prevents subsequent handlers (e.g. audit logger) from executing!
            await handler(event)
```

---

## Root Cause

1. **State Poisoning via Mutable Events**:
   When events are mutable objects (standard mutable dataclasses or dicts), downstream handlers can inadvertently alter event state (e.g., `event.amount = 0`), which corrupts the data seen by subsequent listeners in the chain.

2. **Cascading Failure & Transaction Abort**:
   When an event dispatcher invokes handlers sequentially or concurrently without wrapping each handler in an exception boundary, any failure in a non-critical side-effect (such as sending a welcome email or updating an analytics counter) raises an unhandled exception that propagates back to the domain service, rolling back the primary database transaction or killing the entire request.

---

## Resolution

1. **Immutable, Slotted Domain Event Contracts**:
   All domain events inherit from `DomainEvent` with `@dataclass(slots=True, frozen=True, kw_only=True)`. Any attempt to mutate an event attribute raises `FrozenInstanceError`.

2. **Isolated Coroutine Execution via `asyncio.gather` with Error Safeguards**:
   The `EventDispatcher` runs handlers concurrently while wrapping each invocation inside `_safe_execute`. Transient failures are logged with full event telemetry (`event_type`, `event_id`), incrementing error metrics without interrupting sibling listeners or bubbling back to the core domain transaction.

```python
@dataclass(slots=True, frozen=True, kw_only=True)
class DomainEvent:
    event_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    occurred_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

class EventDispatcher:
    async def dispatch(self, event: DomainEvent) -> None:
        handlers = self._subscribers.get(type(event), [])
        if not handlers:
            return

        tasks = [self._safe_execute(handler, event) for handler in handlers]
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _safe_execute(
        self, handler: Callable[[DomainEvent], Awaitable[None]], event: DomainEvent
    ) -> None:
        try:
            await handler(event)
        except Exception as exc:
            logger.error(
                "Event listener failed: handler=%s, event=%s, error=%s",
                handler.__name__,
                event.event_type,
                exc,
                exc_info=True,
            )
```

---

## Permanent Prevention Rule

- **Codified Rule**: Pattern #161 & #162 in `.agents/skills/fastapi-production/SKILL.md`.
- Always declare domain events as `frozen=True, slots=True`.
- Always isolate event handler exceptions in asynchronous dispatchers to guarantee zero cascading aborts on auxiliary side-effects.
