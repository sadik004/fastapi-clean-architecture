"""Unit and Integration Tests for Domain Events & In-Memory Event Bus Architecture."""

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from app.core.event_bus import EventDispatcher
from app.core.events import (
    DomainEvent,
    OrderPlacedEvent,
    ProductStockDepletedEvent,
    UserRegisteredEvent,
)
from app.events.handlers import (
    clear_recorded_events,
    get_recorded_audit_logs,
    get_recorded_order_analytics,
    get_recorded_welcome_emails,
    record_audit_log_on_registration,
    send_welcome_email_on_registration,
    track_analytics_on_order,
)
from app.repositories.user_repository import UserEntity
from app.schemas.user import UserCreate, UserRole
from app.services.user_service import UserService


@pytest.fixture(autouse=True)
def clean_event_state() -> None:
    """Fixture to ensure clean event bus and recorded sinks for each test."""
    clear_recorded_events()


@pytest.mark.asyncio
async def test_event_dispatcher_multiple_handlers() -> None:
    """Test 1: Publish UserRegisteredEvent; assert multiple registered listeners execute."""
    bus = EventDispatcher()
    bus.subscribe(UserRegisteredEvent, send_welcome_email_on_registration)
    bus.subscribe(UserRegisteredEvent, record_audit_log_on_registration)

    event = UserRegisteredEvent(
        user_id=42,
        email="architect@example.com",
        username="lead_architect",
    )

    await bus.publish(event)

    # Verify both handlers executed
    emails = get_recorded_welcome_emails()
    audits = get_recorded_audit_logs()

    assert len(emails) == 1
    assert emails[0]["user_id"] == 42
    assert emails[0]["email"] == "architect@example.com"
    assert emails[0]["username"] == "lead_architect"

    assert len(audits) == 1
    assert audits[0]["actor_id"] == 42
    assert audits[0]["action"] == "USER_REGISTERED"
    assert audits[0]["actor_username"] == "lead_architect"

    assert bus.published_count == 1
    assert bus.handled_count == 2
    assert bus.error_count == 0


@pytest.mark.asyncio
async def test_event_dispatcher_failure_isolation() -> None:
    """Test 2: A crashing secondary listener must NOT crash other listeners or the publisher."""
    bus = EventDispatcher()
    executed_healthy = False

    async def healthy_handler(ev: UserRegisteredEvent) -> None:
        nonlocal executed_healthy
        executed_healthy = True

    async def failing_email_server_handler(ev: UserRegisteredEvent) -> None:
        raise ConnectionRefusedError("SMTP server down / timeout (Simulated Failure)")

    bus.subscribe(UserRegisteredEvent, failing_email_server_handler)
    bus.subscribe(UserRegisteredEvent, healthy_handler)

    event = UserRegisteredEvent(
        user_id=99,
        email="apprentice@example.com",
        username="apprentice",
    )

    # Publishing must NOT raise an exception
    await bus.publish(event)

    assert executed_healthy is True
    assert bus.published_count == 1
    assert bus.handled_count == 1
    assert bus.error_count == 1


@pytest.mark.asyncio
async def test_user_service_registration_publishes_event() -> None:
    """Test 3: UserService.register_user emits UserRegisteredEvent with correct domain payload."""
    bus = EventDispatcher()
    captured_events: list[UserRegisteredEvent] = []

    async def capture_handler(ev: UserRegisteredEvent) -> None:
        captured_events.append(ev)

    bus.subscribe(UserRegisteredEvent, capture_handler)

    mock_repo = AsyncMock()
    mock_repo.get_by_email.return_value = None
    mock_repo.get_by_username.return_value = None
    mock_repo.create.return_value = UserEntity(
        id=101,
        email="newuser@example.com",
        username="new_user_101",
        password_hash="mock_hashed_password",
        is_active=True,
        created_at=datetime.now(UTC),
        full_name="New User",
        age=28,
        role="user",
    )

    service = UserService(repository=mock_repo, event_dispatcher=bus)

    payload = UserCreate(
        email="newuser@example.com",
        username="new_user_101",
        password="ValidPassword123!",
        password_confirm="ValidPassword123!",
        age=28,
        role=UserRole.USER,
        full_name="New User",
    )

    created = await service.register_user(payload)

    assert created.id == 101
    assert len(captured_events) == 1
    emitted = captured_events[0]
    assert emitted.user_id == 101
    assert emitted.email == "newuser@example.com"
    assert emitted.username == "new_user_101"
    assert emitted.event_type == "UserRegisteredEvent"
    assert emitted.event_id is not None
    assert emitted.occurred_at is not None


def test_domain_event_immutability() -> None:
    """Test 4: Domain events must be frozen and immutable post-creation."""
    event = UserRegisteredEvent(
        user_id=1,
        email="immutable@example.com",
        username="immutable_user",
    )

    with pytest.raises(FrozenInstanceError):
        # Attribute mutation must be forbidden
        event.email = "hacked@example.com"  # type: ignore[misc]

    with pytest.raises(FrozenInstanceError):
        event.event_id = "malicious_id"  # type: ignore[misc]


@pytest.mark.asyncio
async def test_order_placed_and_stock_depleted_events() -> None:
    """Test 5: Verify OrderPlacedEvent and ProductStockDepletedEvent dispatches."""
    bus = EventDispatcher()
    bus.subscribe(OrderPlacedEvent, track_analytics_on_order)

    stock_alert_received: list[ProductStockDepletedEvent] = []

    async def alert_inventory_manager(ev: ProductStockDepletedEvent) -> None:
        stock_alert_received.append(ev)

    bus.subscribe(ProductStockDepletedEvent, alert_inventory_manager)

    order_ev = OrderPlacedEvent(order_id="ord-9988", user_id=7, total_amount=249.99)
    await bus.publish(order_ev)

    analytics = get_recorded_order_analytics()
    assert len(analytics) == 1
    assert analytics[0]["order_id"] == "ord-9988"
    assert analytics[0]["user_id"] == 7
    assert analytics[0]["total_amount"] == 249.99

    stock_ev = ProductStockDepletedEvent(product_id=505, remaining_stock=0)
    await bus.publish(stock_ev)

    assert len(stock_alert_received) == 1
    assert stock_alert_received[0].product_id == 505
    assert stock_alert_received[0].remaining_stock == 0
    assert stock_alert_received[0].event_type == "ProductStockDepletedEvent"


@pytest.mark.asyncio
async def test_event_dispatcher_unsubscribe_and_clear() -> None:
    """Test 6: Verify handler unsubscription and clear mechanics."""
    bus = EventDispatcher()
    invocations = 0

    async def test_handler(ev: DomainEvent) -> None:
        nonlocal invocations
        invocations += 1

    bus.subscribe(OrderPlacedEvent, test_handler)
    await bus.publish(OrderPlacedEvent(order_id="o-1", user_id=1, total_amount=10.0))
    assert invocations == 1

    bus.unsubscribe(OrderPlacedEvent, test_handler)
    await bus.publish(OrderPlacedEvent(order_id="o-2", user_id=1, total_amount=20.0))
    assert invocations == 1  # Not invoked after unsubscribe

    bus.subscribe(OrderPlacedEvent, test_handler)
    bus.clear()
    await bus.publish(OrderPlacedEvent(order_id="o-3", user_id=1, total_amount=30.0))
    assert invocations == 1
    assert bus.published_count == 1  # 1 after clear
