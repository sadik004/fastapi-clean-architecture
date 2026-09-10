"""Order domain service orchestrating order creation with UUIDv7 and O(1) timestamp extraction."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime

import structlog

from app.core.exceptions import OrderNotFoundException, UserNotFoundException
from app.core.identifiers import extract_timestamp_from_uuidv7
from app.core.unit_of_work import UnitOfWorkProtocol
from app.repositories.order_repository import OrderEntity
from app.repositories.outbox_repository import OutboxEventEntity

logger = structlog.get_logger(__name__)


class OrderService:
    """Business service governing order transactions with B-tree optimized UUIDv7 identifiers."""

    def __init__(self, uow: UnitOfWorkProtocol) -> None:
        self._uow = uow

    async def create_order(
        self,
        user_id: int,
        total_amount: float,
        status: str = "pending",
        order_id: uuid.UUID | None = None,
    ) -> OrderEntity:
        """Create and persist a new order within an atomic Unit of Work transaction.

        Verifies that the ordering user exists before creating the order.
        """
        async with self._uow as uow:
            user = await uow.users.get_by_id(user_id)
            if user is None:
                raise UserNotFoundException(user_id=user_id)

            order = await uow.orders.create(
                user_id=user_id,
                total_amount=total_amount,
                status=status,
                order_id=order_id,
            )
            await uow.commit()
            logger.info(
                "order_created",
                order_id=str(order.id),
                user_id=order.user_id,
                total_amount=order.total_amount,
            )
            return order

    async def create_order_with_outbox(
        self,
        user_id: int,
        total_amount: float,
        status: str = "pending",
        order_id: uuid.UUID | None = None,
    ) -> tuple[OrderEntity, OutboxEventEntity]:
        """Create an order and record an outbox event in the same local ACID transaction.

        Guarantees:
        - Atomic Co-location: Either both Order and Outbox event are committed, or neither.
        - Zero Data Loss: Eliminates the dual-write vulnerability between DB and Kafka.
        """
        async with self._uow as uow:
            user = await uow.users.get_by_id(user_id)
            if user is None:
                raise UserNotFoundException(user_id=user_id)

            order = await uow.orders.create(
                user_id=user_id,
                total_amount=total_amount,
                status=status,
                order_id=order_id,
            )
            outbox_event = await uow.outbox.record_event(
                event_type="order.created",
                aggregate_type="order",
                aggregate_id=str(order.id),
                payload={
                    "order_id": str(order.id),
                    "user_id": order.user_id,
                    "total_amount": order.total_amount,
                    "status": order.status,
                    "created_at": order.created_at.isoformat(),
                },
            )
            await uow.commit()
            logger.info(
                "order_created_with_outbox",
                order_id=str(order.id),
                user_id=order.user_id,
                total_amount=order.total_amount,
                outbox_event_id=outbox_event.id,
            )
            return order, outbox_event

    async def get_order_by_id(self, order_id: uuid.UUID) -> OrderEntity:
        """Fetch order details by UUIDv7 primary key."""
        async with self._uow as uow:
            order = await uow.orders.get_by_id(order_id)
            if order is None:
                raise OrderNotFoundException(order_id=str(order_id))
            return order

    async def list_orders_by_user(self, user_id: int, limit: int = 100, offset: int = 0) -> Sequence[OrderEntity]:
        """List user orders sorted by B-Tree monotonic primary key."""
        async with self._uow as uow:
            return await uow.orders.list_by_user_id(user_id=user_id, limit=limit, offset=offset)

    def extract_order_timestamp(self, order_id: uuid.UUID) -> datetime:
        """Extract creation UTC timestamp directly from UUIDv7 bits in strictly O(1) time."""
        return extract_timestamp_from_uuidv7(order_id)
