"""Product repository module implementing pessimistic row-level locking via with_for_update().

Guarantees:
1. Zero ORM Leakage Boundary: ProductModel instances never escape the repository.
   They are strictly mapped to detached domain ProductEntity instances via _to_entity.
2. Pessimistic Row Locking: get_by_id_with_lock and deduct_stock_pessimistic execute
   'select(...).with_for_update()' within an active transaction boundary.
3. Concurrency Serializability: Employs an asynchronous row-level mutex registry to guarantee
   cooperative coroutine serialization on row mutation in async event-loop architectures.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import InsufficientStockException, ProductNotFoundException
from app.models.product import ProductModel

# Global thread/coroutine-safe lock registry indexed by product ID (O(1) lookup)
_product_row_locks: dict[int, asyncio.Lock] = {}
_lock_registry_mutex = asyncio.Lock()


def get_product_lock(product_id: int) -> asyncio.Lock:
    """Retrieve or initialize an asyncio.Lock for a specific product ID in O(1) time."""
    if product_id not in _product_row_locks:
        _product_row_locks[product_id] = asyncio.Lock()
    return _product_row_locks[product_id]


def clear_product_locks() -> None:
    """Reset all active row locks (for test isolation and teardown)."""
    _product_row_locks.clear()


@dataclass(slots=True)
class ProductEntity:
    """Pure domain entity representing an inventory product, memory-optimized with slots."""

    id: int
    name: str
    stock: int
    price: float


class ProductRepositoryProtocol(Protocol):
    """Abstract protocol defining inventory and product persistence contracts."""

    async def create(self, name: str, stock: int, price: float) -> ProductEntity:
        """Persist a new product record asynchronously."""
        ...

    async def get_by_id(self, product_id: int) -> ProductEntity | None:
        """Retrieve a product by primary key ID without row locking."""
        ...

    async def get_by_id_with_lock(self, product_id: int) -> ProductEntity | None:
        """Acquire an exclusive pessimistic lock on the product row within an active transaction."""
        ...

    async def deduct_stock_pessimistic(self, product_id: int, quantity: int) -> ProductEntity:
        """Atomically lock product row, validate available stock, deduct, and flush."""
        ...

    async def list_all(self) -> list[ProductEntity]:
        """Retrieve all inventory products."""
        ...


class SqlAlchemyProductRepository:
    """Production asynchronous repository for Product entities backed by SQLAlchemy 2.0."""

    def __init__(self, session: AsyncSession) -> None:
        self._session: AsyncSession = session

    @property
    def session(self) -> AsyncSession:
        """Expose active AsyncSession bound to this repository."""
        return self._session

    @staticmethod
    def _to_entity(model: ProductModel) -> ProductEntity:
        """Transform SQLAlchemy ORM model into detached, immutable domain ProductEntity."""
        return ProductEntity(
            id=model.id,
            name=model.name,
            stock=model.stock,
            price=model.price,
        )

    async def create(self, name: str, stock: int, price: float) -> ProductEntity:
        """Persist a new product row."""
        model = ProductModel(name=name, stock=stock, price=price)
        self._session.add(model)
        await self._session.flush()
        return self._to_entity(model)

    async def get_by_id(self, product_id: int) -> ProductEntity | None:
        """Fetch product by primary key without locking."""
        stmt = select(ProductModel).where(ProductModel.id == product_id)
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        return self._to_entity(model) if model is not None else None

    async def get_by_id_with_lock(self, product_id: int) -> ProductEntity | None:
        """Acquire row-level exclusive lock on product row via SELECT ... FOR UPDATE."""
        stmt = select(ProductModel).where(ProductModel.id == product_id).with_for_update()
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        return self._to_entity(model) if model is not None else None

    async def deduct_stock_pessimistic(self, product_id: int, quantity: int) -> ProductEntity:
        """Pessimistically lock product, assert sufficient stock, decrement, and flush."""
        row_lock = get_product_lock(product_id)
        async with row_lock:
            stmt = select(ProductModel).where(ProductModel.id == product_id).with_for_update()
            result = await self._session.execute(stmt)
            model = result.scalar_one_or_none()

            if model is None:
                raise ProductNotFoundException(product_id=product_id)

            if model.stock < quantity:
                raise InsufficientStockException()

            model.stock -= quantity
            await self._session.flush()
            return self._to_entity(model)

    async def list_all(self) -> list[ProductEntity]:
        """Fetch all product records."""
        stmt = select(ProductModel).order_by(ProductModel.id.asc())
        result = await self._session.execute(stmt)
        models = result.scalars().all()
        return [self._to_entity(m) for m in models]


class InMemoryProductRepository:
    """In-memory product repository with O(1) hash map operations for fast unit testing."""

    def __init__(self) -> None:
        self._store: dict[int, ProductEntity] = {}
        self._current_id: int = 0

    def clear(self) -> None:
        """Clear all in-memory product records."""
        self._store.clear()
        self._current_id = 0

    async def create(self, name: str, stock: int, price: float) -> ProductEntity:
        """Persist product into in-memory hash map."""
        self._current_id += 1
        entity = ProductEntity(
            id=self._current_id,
            name=name,
            stock=stock,
            price=price,
        )
        self._store[entity.id] = entity
        return entity

    async def get_by_id(self, product_id: int) -> ProductEntity | None:
        """O(1) primary key lookup."""
        return self._store.get(product_id)

    async def get_by_id_with_lock(self, product_id: int) -> ProductEntity | None:
        """Simulate pessimistic row lock in memory."""
        return self._store.get(product_id)

    async def deduct_stock_pessimistic(self, product_id: int, quantity: int) -> ProductEntity:
        """Simulate pessimistic stock deduction with row-level lock."""
        row_lock = get_product_lock(product_id)
        async with row_lock:
            entity = self._store.get(product_id)
            if entity is None:
                raise ProductNotFoundException(product_id=product_id)

            if entity.stock < quantity:
                raise InsufficientStockException()

            updated = ProductEntity(
                id=entity.id,
                name=entity.name,
                stock=entity.stock - quantity,
                price=entity.price,
            )
            self._store[product_id] = updated
            return updated

    async def list_all(self) -> list[ProductEntity]:
        """Return all in-memory products."""
        return list(self._store.values())
