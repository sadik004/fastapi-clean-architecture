"""Product Service demonstrating Database Read/Write Replica Splitting & Lag Guard."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ProductNotFoundException
from app.core.routing_session import DatabaseRole, RoutingUnitOfWork
from app.models.product import ProductModel
from app.repositories.product_repository import ProductEntity, SqlAlchemyProductRepository


class ProductService:
    """Service providing catalog queries via read replicas and mutations via primary master."""

    __slots__ = ("_read_session", "_uow")

    def __init__(
        self,
        read_session: AsyncSession | None = None,
        uow: RoutingUnitOfWork | None = None,
    ) -> None:
        self._read_session = read_session
        self._uow = uow or RoutingUnitOfWork()

    async def get_product(
        self, product_id: int
    ) -> tuple[ProductEntity, DatabaseRole]:
        """Fetch a product by ID from the Read Replica engine in O(1) time."""
        if self._read_session is not None:
            repo = SqlAlchemyProductRepository(session=self._read_session)
            entity = await repo.get_by_id(product_id)
            if entity is None:
                raise ProductNotFoundException(product_id=product_id)
            return entity, DatabaseRole.REPLICA

        # Fallback to UoW read session if standalone session not provided
        async with self._uow as uow:
            entity = await uow.products.get_by_id(product_id)
            if entity is None:
                raise ProductNotFoundException(product_id=product_id)
            return entity, uow.current_read_target

    async def list_products(
        self, limit: int = 50
    ) -> tuple[list[dict[str, Any]], DatabaseRole]:
        """List products from the Read Replica engine."""
        session = self._read_session
        if session is not None:
            stmt = select(ProductModel).limit(limit)
            result = await session.execute(stmt)
            models = result.scalars().all()
            items = [
                {"id": m.id, "name": m.name, "stock": m.stock, "price": m.price}
                for m in models
            ]
            return items, DatabaseRole.REPLICA

        async with self._uow as uow:
            stmt = select(ProductModel).limit(limit)
            result = await uow.execute_read(stmt)
            models = result.scalars().all()
            items = [
                {"id": m.id, "name": m.name, "stock": m.stock, "price": m.price}
                for m in models
            ]
            return items, uow.current_read_target

    async def create_product(
        self, name: str, stock: int, price: float
    ) -> tuple[ProductEntity, DatabaseRole]:
        """Persist a new product strictly via Primary / Writer master engine."""
        async with self._uow as uow:
            entity = await uow.products.create(name=name, stock=stock, price=price)
            await uow.commit()
            return entity, DatabaseRole.PRIMARY

    async def create_and_fetch_product(
        self, name: str, stock: int, price: float
    ) -> tuple[ProductEntity, DatabaseRole, bool]:
        """Demonstrate Read-Your-Own-Writes lag guard.

        After executing a write mutation inside the transaction boundary,
        subsequent reads within the same context stick to the Primary engine,
        preventing stale data reads caused by asynchronous replication lag.
        """
        async with self._uow as uow:
            created = await uow.products.create(name=name, stock=stock, price=price)
            await uow.commit()

            # Read-Your-Own-Writes: uow.has_written is True, so reading sticks to Primary
            target = uow.current_read_target
            has_written = uow.has_written
            fetched = await uow.products.get_by_id(created.id)
            if fetched is None:
                raise ProductNotFoundException(product_id=created.id)

            return fetched, target, has_written
