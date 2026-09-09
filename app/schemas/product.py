"""Pydantic schemas for Product catalog and pessimistic checkout contracts."""

from pydantic import BaseModel, ConfigDict, Field


class ProductCreate(BaseModel):
    """Payload schema for registering a new inventory product."""

    name: str = Field(..., min_length=1, max_length=100, description="Product display name")
    stock: int = Field(..., ge=0, description="Initial stock level (non-negative integer)")
    price: float = Field(..., gt=0.0, description="Unit price (strictly positive)")


class ProductResponse(BaseModel):
    """Public response projection for product catalog details."""

    id: int = Field(..., description="Unique product identifier")
    name: str = Field(..., description="Product name")
    stock: int = Field(..., description="Current available stock")
    price: float = Field(..., description="Unit price")

    model_config = ConfigDict(from_attributes=True)


class CheckoutRequest(BaseModel):
    """Payload schema for inventory checkout requests."""

    quantity: int = Field(default=1, gt=0, description="Quantity to purchase / deduct")


class CheckoutResponse(BaseModel):
    """Response returned upon successful stock deduction."""

    product_id: int = Field(..., description="Product identifier")
    name: str = Field(..., description="Product name")
    remaining_stock: int = Field(..., description="Remaining available inventory stock")
    deducted_quantity: int = Field(..., description="Quantity deducted in this transaction")
    message: str = Field(default="Checkout successful", description="Status message")
