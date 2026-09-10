"""Schemas for Database Read/Write Replica Routing Probes and Telemetry."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class DatabaseProbeResponse(BaseModel):
    """Telemetry response returned by database routing probe endpoints."""

    target_engine: str = Field(..., description="Target database engine: PRIMARY or REPLICA")
    query_type: str = Field(..., description="Query operation type: READ, WRITE, or READ_AFTER_WRITE")
    read_your_own_writes_active: bool = Field(
        default=False,
        description="Whether Read-Your-Own-Writes stickiness was activated to prevent replication lag",
    )
    data: Any = Field(..., description="Result payload produced by the query")
    executed_at: datetime = Field(..., description="Timestamp of query completion")

    model_config = ConfigDict(from_attributes=True)


class CreateProductProbeRequest(BaseModel):
    """Payload to simulate creating a product on the primary master database."""

    name: str = Field(..., min_length=2, max_length=100, description="Product title")
    stock: int = Field(default=10, ge=0, description="Initial stock quantity")
    price: float = Field(default=29.99, gt=0.0, description="Product price")


class CreateProductProbeResponse(BaseModel):
    """Response returned upon persisting product via Primary master engine."""

    product_id: int = Field(..., description="Assigned product ID")
    name: str = Field(..., description="Product name")
    stock: int = Field(..., description="Available inventory")
    price: float = Field(..., description="Product price")
    target_engine: str = Field(default="PRIMARY", description="Target database engine that handled the write")
    executed_at: datetime = Field(..., description="Timestamp of write commitment")

    model_config = ConfigDict(from_attributes=True)


class PoolTelemetry(BaseModel):
    """Connection pool metrics for a single database engine."""

    pool_type: str
    pool_size: int
    checked_in_connections: int
    checked_out_connections: int
    overflow_connections: int
    total_open_connections: int


class DualPoolStatusResponse(BaseModel):
    """Telemetry response detailing Primary and Replica connection pools."""

    primary: PoolTelemetry
    replica: PoolTelemetry

    model_config = ConfigDict(from_attributes=True)
