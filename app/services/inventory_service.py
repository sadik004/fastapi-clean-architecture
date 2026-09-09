"""Inventory domain service orchestrating stock management and pessimistic checkout flows."""

from __future__ import annotations

from app.core.exceptions import ProductNotFoundException
from app.core.unit_of_work import UnitOfWorkProtocol
from app.repositories.product_repository import ProductEntity


class InventoryService:
    """Domain service managing product lifecycle and atomic inventory mutations under Unit of Work."""

    def __init__(self, uow: UnitOfWorkProtocol) -> None:
        self._uow: UnitOfWorkProtocol = uow

    async def create_product(self, name: str, stock: int, price: float) -> ProductEntity:
        """Create and persist a new product within a transactional boundary."""
        async with self._uow:
            product = await self._uow.products.create(name=name, stock=stock, price=price)
            await self._uow.commit()
            return product

    async def get_product_by_id(self, product_id: int) -> ProductEntity:
        """Retrieve product by ID or raise ProductNotFoundException."""
        async with self._uow:
            product = await self._uow.products.get_by_id(product_id=product_id)
            if product is None:
                raise ProductNotFoundException(product_id=product_id)
            return product

    async def list_products(self) -> list[ProductEntity]:
        """Fetch all available products from inventory."""
        async with self._uow:
            return await self._uow.products.list_all()

    async def checkout_product(self, product_id: int, quantity: int = 1) -> ProductEntity:
        """Atomically deduct stock using pessimistic locking (with_for_update) within active UoW transaction.

        Lock Lifecycle:
        1. Open transaction boundary ('async with self._uow:').
        2. Acquire exclusive row-level lock on product row via SELECT ... FOR UPDATE.
        3. Assert stock >= quantity (raises InsufficientStockException if depleted).
        4. Decrement stock and flush session.
        5. Commit transaction boundary, persisting new stock level and releasing the row lock.
        """
        async with self._uow:
            updated_product = await self._uow.products.deduct_stock_pessimistic(
                product_id=product_id,
                quantity=quantity,
            )
            await self._uow.commit()
            return updated_product
