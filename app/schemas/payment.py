"""Pydantic v2 schemas for Payment processing and idempotency verification."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class PaymentChargeRequest(BaseModel):
    """Client request schema for processing a payment charge."""

    order_id: str = Field(
        ...,
        min_length=3,
        max_length=100,
        description="Unique business order reference",
    )
    amount: float = Field(
        ...,
        gt=0.0,
        description="Monetary charge amount (must be positive)",
    )
    currency: str = Field(
        default="BDT",
        min_length=3,
        max_length=10,
        description="Three-letter ISO currency code",
    )


class PaymentChargeResponse(BaseModel):
    """Response schema returned upon successful payment charge execution."""

    charge_id: str = Field(..., description="Unique generated payment transaction ID")
    order_id: str = Field(..., description="Business order reference")
    amount: float = Field(..., description="Charged monetary amount")
    currency: str = Field(..., description="Currency used for payment")
    status: str = Field(default="succeeded", description="Payment processing status")
    created_at: datetime = Field(..., description="Timestamp of payment capture")

    model_config = ConfigDict(from_attributes=True)
