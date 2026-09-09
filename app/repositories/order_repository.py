"""Order repository implementing persistence with UUIDv7 Primary Keys.

Guarantees:
1. Zero ORM Leakage: OrderModel instances never leave this repository.
   They are strictly converted into detached domain OrderEntity instances via _to_entity.
2. B-Tree Clustered Locality: Insertions are ordered by monotonic timestamps.
3. Dual-Mode Repositories: Provides both SqlAlchemyOrderRepository and InMemoryOrderRepository.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.identifiers import generate_uuidv7
from app.models.order import OrderModel


@dataclass(slots=True)
class OrderEntity:
    """Pure domain entity representing an order, memory-optimized with slots."""

    id: uuid.UUID
    user_id: int
    total_amount: float
    status: str
    created_at: datetime


class OrderRepositoryProtocol(Protocol):
    """Abstract protocol defining order persistence contracts."""

    async def create(
        self,
        user_id: int,
        total_amount: float,
        status: str = "pending",
        order_id: uuid.UUID | None = None,
    ) -> OrderEntity:
        """Persist a new order record asynchronously."""
        ...

    async def get_by_id(self, order_id: uuid.UUID) -> OrderEntity | None:
        """Retrieve an order by primary key ID."""
        ...

    async def list_by_user_id(self, user_id: int, limit: int = 100, offset: int = 0) -> Sequence[OrderEntity]:
        """List orders belonging to a specific user with bounded pagination."""
        ...


class SqlAlchemyOrderRepository:
    """Asynchronous SQLAlchemy 2.0 Order repository implementation."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def _to_entity(self, model: OrderModel) -> OrderEntity:
        """Map ORM OrderModel to detached pure domain OrderEntity."""
        return OrderEntity(
            id=model.id,
            user_id=model.user_id,
            total_amount=model.total_amount,
            status=model.status,
            created_at=model.created_at,
        )

    async def create(
        self,
        user_id: int,
        total_amount: float,
        status: str = "pending",
        order_id: uuid.UUID | None = None,
    ) -> OrderEntity:
        """Persist a new order record with automatic or provided UUIDv7."""
        assigned_id = order_id or generate_uuidv7()
        order_model = OrderModel(
            id=assigned_id,
            user_id=user_id,
            total_amount=total_amount,
            status=status,
        )
        self._session.add(order_model)
        await self._session.flush()
        return self._to_entity(order_model)

    async def get_by_id(self, order_id: uuid.UUID) -> OrderEntity | None:
        """Retrieve an order by primary key ID."""
        stmt = select(OrderModel).where(OrderModel.id == order_id)
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        if model is None:
            return None
        return self._to_entity(model)

    async def list_by_user_id(self, user_id: int, limit: int = 100, offset: int = 0) -> Sequence[OrderEntity]:
        """List orders sorted by monotonic primary key (B-Tree index order)."""
        stmt = (
            select(OrderModel)
            .where(OrderModel.user_id == user_id)
            .order_by(OrderModel.id.asc())
            .limit(limit)
            .offset(offset)
        )
        result = await self._session.execute(stmt)
        models = result.scalars().all()
        return [self._to_entity(m) for m in models]


class InMemoryOrderRepository:
    """High-speed in-memory repository for test isolation and benchmarking."""

    def __init__(self) -> None:
        self._store: dict[uuid.UUID, OrderEntity] = {}

    async def create(
        self,
        user_id: int,
        total_amount: float,
        status: str = "pending",
        order_id: uuid.UUID | None = None,
    ) -> OrderEntity:
        assigned_id = order_id or generate_uuidv7()
        entity = OrderEntity(
            id=assigned_id,
            user_id=user_id,
            total_amount=total_amount,
            status=status,
            created_at=datetime.now(UTC),
        )
        self._store[assigned_id] = entity
        return entity

    async def get_by_id(self, order_id: uuid.UUID) -> OrderEntity | None:
        return self._store.get(order_id)

    async def list_by_user_id(self, user_id: int, limit: int = 100, offset: int = 0) -> Sequence[OrderEntity]:
        matching = [o for o in self._store.values() if o.user_id == user_id]
        matching.sort(key=lambda o: o.id)
        return matching[offset : offset + limit]
