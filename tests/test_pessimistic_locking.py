"""Test Suite for Day 40: Pessimistic Locking Architecture (Inventory Stock Blocking via with_for_update).

Verifies:
1. Sequential Stock Deduction:
   - Initial stock 10 -> deduct 3 -> remaining stock is exactly 7.
2. Insufficient Stock Exception Rejection:
   - Initial stock 2 -> request quantity 5 -> raises InsufficientStockException (HTTP 400).
   - Confirms error envelope code is 'INSUFFICIENT_STOCK'.
3. Concurrent Flash Sale Race Test (Mathematical Invariant):
   - Seed product with initial stock = 5.
   - Launch 15 concurrent checkout requests (each requesting quantity = 1) via asyncio.gather.
   - Mathematical Invariant Proof:
     * EXACTLY 5 requests succeed with HTTP 200 OK.
     * EXACTLY 10 requests fail with HTTP 400 Bad Request ('INSUFFICIENT_STOCK').
     * Final persisted stock in the database is EXACTLY 0 (Zero Overselling Guarantee).
4. Direct Repository & Service Unit Coverage:
   - Validates SqlAlchemyProductRepository and InventoryService directly under UnitOfWork transactions.
5. Entity Not Found & Validation Checks:
   - Non-existent product ID checkout yields HTTP 404.
   - Invalid quantity (<= 0) yields HTTP 422 Unprocessable Entity.
"""

from __future__ import annotations

import asyncio

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.core.database import async_session_factory
from app.core.exceptions import InsufficientStockException, ProductNotFoundException
from app.core.unit_of_work import InMemoryUnitOfWork
from app.main import app
from app.models.product import ProductModel
from app.repositories.product_repository import (
    SqlAlchemyProductRepository,
)
from app.services.inventory_service import InventoryService

# ============================================================================
# 1. Direct SQLAlchemy Repository & UoW Unit Tests
# ============================================================================


@pytest.mark.asyncio
async def test_sqlalchemy_product_repo_sequential_deduction() -> None:
    """Verify sequential stock deduction via SqlAlchemyProductRepository within UoW transaction."""
    async with async_session_factory() as session:
        repo = SqlAlchemyProductRepository(session=session)
        created = await repo.create(name="Mechanical Keyboard", stock=10, price=89.99)
        await session.commit()
        product_id = created.id
        assert created.stock == 10

    # Execute deduction of quantity 3
    async with async_session_factory() as session:
        repo = SqlAlchemyProductRepository(session=session)
        updated = await repo.deduct_stock_pessimistic(product_id=product_id, quantity=3)
        await session.commit()
        assert updated.stock == 7

    # Verify persisted state directly in database
    async with async_session_factory() as session:
        stmt = select(ProductModel).where(ProductModel.id == product_id)
        result = await session.execute(stmt)
        model = result.scalar_one()
        assert model.stock == 7


@pytest.mark.asyncio
async def test_sqlalchemy_product_repo_insufficient_stock_rejection() -> None:
    """Verify repo raises InsufficientStockException when quantity exceeds available stock."""
    async with async_session_factory() as session:
        repo = SqlAlchemyProductRepository(session=session)
        created = await repo.create(name="Gaming Mouse", stock=2, price=49.99)
        await session.commit()
        product_id = created.id

    async with async_session_factory() as session:
        repo = SqlAlchemyProductRepository(session=session)
        with pytest.raises(InsufficientStockException) as exc_info:
            await repo.deduct_stock_pessimistic(product_id=product_id, quantity=5)
        assert exc_info.value.code == "INSUFFICIENT_STOCK"

    # Verify stock remains untouched at 2
    async with async_session_factory() as session:
        stmt = select(ProductModel).where(ProductModel.id == product_id)
        result = await session.execute(stmt)
        model = result.scalar_one()
        assert model.stock == 2


@pytest.mark.asyncio
async def test_sqlalchemy_product_repo_not_found() -> None:
    """Verify repo raises ProductNotFoundException for unknown ID."""
    async with async_session_factory() as session:
        repo = SqlAlchemyProductRepository(session=session)
        with pytest.raises(ProductNotFoundException) as exc_info:
            await repo.deduct_stock_pessimistic(product_id=999999, quantity=1)
        assert exc_info.value.code == "ENTITY_NOT_FOUND"


# ============================================================================
# 2. In-Memory UnitOfWork & Service Layer Tests
# ============================================================================


@pytest.mark.asyncio
async def test_inventory_service_with_in_memory_uow() -> None:
    """Verify InventoryService works seamlessly with InMemoryUnitOfWork."""
    uow = InMemoryUnitOfWork()
    service = InventoryService(uow=uow)

    product = await service.create_product(name="Headphones", stock=10, price=199.99)
    assert product.id == 1
    assert product.stock == 10

    # Sequential deduction
    deducted = await service.checkout_product(product_id=product.id, quantity=4)
    assert deducted.stock == 6

    # Rejection on deficit
    with pytest.raises(InsufficientStockException):
        await service.checkout_product(product_id=product.id, quantity=10)


# ============================================================================
# 3. HTTP Transport & API Integration Tests
# ============================================================================


@pytest.mark.asyncio
async def test_api_checkout_sequential_and_insufficient_stock() -> None:
    """Verify HTTP API endpoints for product registration, retrieval, and sequential checkout."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Register a new product
        create_res = await client.post(
            "/products/",
            json={"name": "Ultrawide Monitor", "stock": 10, "price": 450.0},
        )
        assert create_res.status_code == 201
        created_data = create_res.json()
        product_id = created_data["id"]
        assert created_data["stock"] == 10

        # 2. Fetch product by ID
        get_res = await client.get(f"/products/{product_id}")
        assert get_res.status_code == 200
        assert get_res.json()["name"] == "Ultrawide Monitor"

        # 3. Sequential deduction: stock 10 -> deduct 3 -> remaining stock 7
        checkout_res1 = await client.post(
            f"/products/{product_id}/checkout",
            json={"quantity": 3},
        )
        assert checkout_res1.status_code == 200
        data1 = checkout_res1.json()
        assert data1["remaining_stock"] == 7
        assert data1["deducted_quantity"] == 3

        # 4. Insufficient stock request: remaining 7 -> request 10 -> HTTP 400
        checkout_res2 = await client.post(
            f"/products/{product_id}/checkout",
            json={"quantity": 10},
        )
        assert checkout_res2.status_code == 400
        error_body = checkout_res2.json()
        assert error_body["error"]["code"] == "INSUFFICIENT_STOCK"
        assert "Requested quantity exceeds available stock." in error_body["error"]["message"]

        # 5. Verify stock was not deducted on failure
        verify_res = await client.get(f"/products/{product_id}")
        assert verify_res.status_code == 200
        assert verify_res.json()["stock"] == 7


@pytest.mark.asyncio
async def test_api_checkout_nonexistent_product_and_invalid_quantity() -> None:
    """Verify error responses for missing product and invalid checkout quantity."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Non-existent product -> HTTP 404
        res_404 = await client.post("/products/999999/checkout", json={"quantity": 1})
        assert res_404.status_code == 404
        assert res_404.json()["error"]["code"] == "ENTITY_NOT_FOUND"

        # Invalid quantity (0) -> HTTP 422
        res_422 = await client.post("/products/1/checkout", json={"quantity": 0})
        assert res_422.status_code == 422


# ============================================================================
# 4. Concurrent Flash Sale Race Test (Mathematical Invariant)
# ============================================================================


@pytest.mark.asyncio
async def test_concurrent_flash_sale_race_condition_zero_overselling() -> None:
    """Mathematical Invariant: 15 concurrent checkout requests competing for 5 units of stock.

    Guarantees:
    - EXACTLY 5 requests succeed (HTTP 200).
    - EXACTLY 10 requests fail with InsufficientStockException (HTTP 400).
    - Final stock in database is EXACTLY 0 (Never -1, proving zero overselling!).
    """
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Seed product with exactly 5 units of stock
        create_res = await client.post(
            "/products/",
            json={"name": "Limited Edition Flash Sale Item", "stock": 5, "price": 99.99},
        )
        assert create_res.status_code == 201
        product_id = create_res.json()["id"]

        concurrency_count = 15

        async def send_checkout(index: int) -> int:
            res = await client.post(
                f"/products/{product_id}/checkout",
                json={"quantity": 1},
            )
            return res.status_code

        # Fire all 15 concurrent checkout requests simultaneously via asyncio.gather
        tasks = [send_checkout(i) for i in range(concurrency_count)]
        status_codes = await asyncio.gather(*tasks)

        # Assert Mathematical Invariants
        success_count = status_codes.count(200)
        failure_count = status_codes.count(400)

        assert success_count == 5, (
            f"Expected EXACTLY 5 successful checkouts (HTTP 200), got {success_count}. Statuses: {status_codes}"
        )
        assert failure_count == 10, (
            f"Expected EXACTLY 10 rejections (HTTP 400), got {failure_count}. Statuses: {status_codes}"
        )

        # Verify final stock in the database is strictly 0
        final_res = await client.get(f"/products/{product_id}")
        assert final_res.status_code == 200
        final_stock = final_res.json()["stock"]
        assert final_stock == 0, f"Expected final stock to be EXACTLY 0, but found {final_stock}!"
