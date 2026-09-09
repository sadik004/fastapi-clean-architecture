"""Router endpoints for Product inventory management and pessimistic checkout locking."""

from typing import Annotated

from fastapi import APIRouter, Depends, Path, status

from app.core.dependencies import get_inventory_service
from app.schemas.product import (
    CheckoutRequest,
    CheckoutResponse,
    ProductCreate,
    ProductResponse,
)
from app.services.inventory_service import InventoryService

router = APIRouter(prefix="/products", tags=["Products & Inventory"])


@router.post(
    "/",
    response_model=ProductResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register a new inventory product",
)
async def create_product_endpoint(
    payload: ProductCreate,
    service: Annotated[InventoryService, Depends(get_inventory_service)],
) -> ProductResponse:
    """Create a new product record in the persistent inventory store."""
    product = await service.create_product(
        name=payload.name,
        stock=payload.stock,
        price=payload.price,
    )
    return ProductResponse.model_validate(product)


@router.get(
    "/{product_id}",
    response_model=ProductResponse,
    status_code=status.HTTP_200_OK,
    summary="Retrieve product by identifier",
)
async def get_product_endpoint(
    product_id: Annotated[int, Path(..., ge=1, description="Unique product ID")],
    service: Annotated[InventoryService, Depends(get_inventory_service)],
) -> ProductResponse:
    """Fetch product inventory details in O(1) time."""
    product = await service.get_product_by_id(product_id=product_id)
    return ProductResponse.model_validate(product)


@router.post(
    "/{product_id}/checkout",
    response_model=CheckoutResponse,
    status_code=status.HTTP_200_OK,
    summary="Atomic pessimistic checkout with row-level locking",
)
async def checkout_product_endpoint(
    product_id: Annotated[int, Path(..., ge=1, description="Product ID to purchase")],
    payload: CheckoutRequest,
    service: Annotated[InventoryService, Depends(get_inventory_service)],
) -> CheckoutResponse:
    """Perform atomic stock deduction protected by SQLAlchemy with_for_update row lock.

    Guarantees:
    - Atomicity: Deduct stock within an active Unit of Work transaction.
    - Zero Overselling: If stock < quantity, raises InsufficientStockException (HTTP 400).
    - Lock Isolation: Releases row lock strictly upon transaction commit or rollback.
    """
    updated_product = await service.checkout_product(
        product_id=product_id,
        quantity=payload.quantity,
    )
    return CheckoutResponse(
        product_id=updated_product.id,
        name=updated_product.name,
        remaining_stock=updated_product.stock,
        deducted_quantity=payload.quantity,
        message="Checkout successful",
    )
