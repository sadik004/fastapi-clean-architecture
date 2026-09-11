"""Product Service demonstrating Database Read/Write Replica Splitting & Lag Guard."""

from __future__ import annotations

from typing import Any

from app.core.exceptions import ProductNotFoundException
from app.core.protocols import ProductRepositoryProtocol
from app.core.routing_session import DatabaseRole, RoutingUnitOfWork
from app.repositories.product_repository import ProductEntity


class ProductService:
    """Service providing catalog queries via read replicas and mutations via primary master."""

    __slots__ = ("_repo", "_uow")

    def __init__(
        self,
        repository: ProductRepositoryProtocol | None = None,
        uow: RoutingUnitOfWork | None = None,
    ) -> None:
        self._repo = repository
        self._uow = uow or RoutingUnitOfWork()

    async def get_product(
        self, product_id: int
    ) -> tuple[ProductEntity, DatabaseRole]:
        """Fetch a product by ID from the Read Replica engine in O(1) time."""
        if self._repo is not None:
            entity = await self._repo.get_by_id(product_id)
            if entity is None:
                raise ProductNotFoundException(product_id=product_id)
            return entity, DatabaseRole.REPLICA

        # Fallback to UoW read session if standalone repository not provided
        async with self._uow as uow:
            entity = await uow.products.get_by_id(product_id)
            if entity is None:
                raise ProductNotFoundException(product_id=product_id)
            return entity, uow.current_read_target

    async def list_products(
        self, limit: int = 50
    ) -> tuple[list[dict[str, Any]], DatabaseRole]:
        """List products from the Read Replica engine."""
        if self._repo is not None:
            entities = await self._repo.list_all()
            items = [
                {"id": e.id, "name": e.name, "stock": e.stock, "price": e.price}
                for e in entities[:limit]
            ]
            return items, DatabaseRole.REPLICA

        async with self._uow as uow:
            entities = await uow.products.list_all()
            items = [
                {"id": e.id, "name": e.name, "stock": e.stock, "price": e.price}
                for e in entities[:limit]
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
