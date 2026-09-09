# Day 58: Domain Events & Decoupled Architecture (Pure Domain Event Dispatcher & In-Memory Event Bus)

## Architectural Objective
Eliminate direct cross-domain coupling and "god service" orchestration by introducing a pure **Domain Events Architecture** powered by an asynchronous **In-Memory Event Bus & Dispatcher**. Core domain mutation services (such as [`UserService.register_user`](file:///e:/FastApi1/app/services/user_service.py)) emit frozen, immutable event contracts ([`DomainEvent`](file:///e:/FastApi1/app/core/events.py)) without any direct knowledge of secondary side-effects (e.g. welcome email dispatch, audit logging, or business intelligence metrics).

---

## 1. Domain Event Data Contracts ([`app/core/events.py`](file:///e:/FastApi1/app/core/events.py))
- **Immutability Invariant**: Domain events represent irreversible historical facts. They are constructed using `@dataclass(slots=True, frozen=True, kw_only=True)`.
- **Base Domain Contract**:
  - `event_id: str`: Unique identifier (UUID4 / UUIDv7) for end-to-end tracing and deduplication.
  - `occurred_at: datetime`: UTC timestamp recorded at event instantiation.
  - `event_type: str`: Self-describing event discriminator, auto-assigned from the concrete class name.
- **Concrete Domain Payloads**:
  - `UserRegisteredEvent(DomainEvent)`: `user_id: int`, `email: str`, `username: str`.
  - `OrderPlacedEvent(DomainEvent)`: `order_id: str`, `user_id: int`, `total_amount: float`.
  - `ProductStockDepletedEvent(DomainEvent)`: `product_id: int`, `remaining_stock: int`.

---

## 2. In-Memory Event Bus & Dispatcher ([`app/core/event_bus.py`](file:///e:/FastApi1/app/core/event_bus.py))
- **Registry Mechanics**:
  - Maintained as `defaultdict(list)`: mapping event classes (`type[DomainEvent]`) to a collection of asynchronous callables (`Callable[[Any], Awaitable[None]]`).
  - O(1) listener subscription (`subscribe`) and unsubscription (`unsubscribe`).
- **Strict Failure Isolation**:
  - Handlers are scheduled concurrently via `asyncio.gather`.
  - Every individual handler invocation is wrapped within an isolated `_safe_execute` boundary.
  - If a secondary listener crashes (e.g. SMTP connection timeout, external webhook error), the failure is logged with full stack telemetry without terminating other concurrent listeners and without bubbling back to abort the primary domain caller.
- **Telemetry & Lifecycle**:
  - Provides metrics for `published_count`, `handled_count`, and `error_count`.
  - Supports `clear()` for clean state management during unit test teardown.
  - Global singleton access through `get_event_dispatcher()`.

---

## 3. Decoupled Domain Event Handlers ([`app/events/handlers.py`](file:///e:/FastApi1/app/events/handlers.py))
- Secondary side-effects live in dedicated listener functions:
  - `send_welcome_email_on_registration`: Async welcome email dispatch.
  - `record_audit_log_on_registration`: Persistent security audit trail insertion.
  - `track_analytics_on_order`: E-commerce conversion and revenue analytics emission.
- Startup Hook:
  - `register_default_event_handlers` automatically wires domain listeners into the global dispatcher during FastAPI `lifespan` startup in [`app/main.py`](file:///e:/FastApi1/app/main.py).

---

## 4. Domain Service Refactoring ([`app/services/user_service.py`](file:///e:/FastApi1/app/services/user_service.py))
- **Zero Side-Effect Leakage**:
  - `UserService` depends exclusively on `EventDispatcher` (injected via constructor).
  - In `register_user`:
    ```python
    if self._event_dispatcher is not None:
        event = UserRegisteredEvent(
            user_id=created_user.id,
            email=created_user.email,
            username=created_user.username,
        )
        await self._event_dispatcher.publish(event)
    ```
  - The service is completely decoupled from SMTP, SMS gateways, analytics trackers, or external logging infrastructure.

---

## 5. Verification Results
- **Day 58 Targeted Suite** ([`tests/test_domain_events.py`](file:///e:/FastApi1/tests/test_domain_events.py)): 6 tests passing in 0.65s.
- **Combined Messaging & Events Suite**: 52 tests passing in 9.01s.
- **Static Analysis**: `ruff check` and `mypy --strict` 100% clean across 180 source files.
