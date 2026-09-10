"""Pydantic Schemas for Catalog Items and Database Query Plan Diagnostics."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class CatalogItemCreate(BaseModel):
    """Schema for creating a catalog item with multi-index attributes."""

    sku: str = Field(..., min_length=2, max_length=50, description="Unique SKU code (B-Tree indexed)")
    name: str = Field(..., min_length=1, max_length=255, description="Item display name")
    category: str = Field(..., min_length=1, max_length=100, description="Product category (Composite B-Tree)")
    price: float = Field(..., ge=0.0, description="Unit price (Composite B-Tree)")
    barcode: str = Field(..., min_length=1, max_length=100, description="Barcode or UUID (Hash indexed)")
    metadata_json: dict[str, Any] = Field(
        default_factory=dict,
        description="Semi-structured attributes (GIN JSONB indexed)",
    )
    tags: list[str] = Field(
        default_factory=list,
        description="String tags list (GIN Array indexed)",
    )


class CatalogItemResponse(BaseModel):
    """DTO representing a persisted catalog item with indexing telemetry."""

    id: int
    sku: str
    name: str
    category: str
    price: float
    barcode: str
    metadata_json: dict[str, Any]
    tags: list[str]
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ExplainQueryRequest(BaseModel):
    """Request payload for arbitrary SQL SELECT execution plan analysis."""

    query: str = Field(..., min_length=6, description="SQL SELECT statement to explain")
    params: dict[str, Any] = Field(default_factory=dict, description="Named parameters for bound query execution")


class QueryPlanNode(BaseModel):
    """Represents an execution plan node in the query tree."""

    node_type: str = Field(..., description="Node scan archetype (e.g. Seq Scan, Index Scan, Bitmap Index Scan)")
    relation_name: str | None = Field(None, description="Target database table name")
    index_name: str | None = Field(None, description="Associated index name if resolved via index")
    total_cost: float = Field(default=0.0, description="Estimated total cost units from database planner")
    plan_rows: int = Field(default=0, description="Estimated row count")
    actual_total_time_ms: float = Field(default=0.0, description="Measured execution time in milliseconds")
    actual_rows: int = Field(default=0, description="Actual rows filtered/returned")


class QueryPlanReport(BaseModel):
    """Comprehensive diagnostic report parsed from EXPLAIN (ANALYZE, BUFFERS) or EXPLAIN QUERY PLAN."""

    dialect: str = Field(..., description="Database engine dialect (postgresql | sqlite)")
    scan_types: list[str] = Field(default_factory=list, description="Unique scan algorithms detected in plan tree")
    primary_scan_type: str = Field(..., description="Primary data retrieval method")
    total_cost: float = Field(default=0.0, description="Cumulative query cost from query optimizer")
    planning_time_ms: float = Field(default=0.0, description="Planner optimization time in milliseconds")
    execution_time_ms: float = Field(default=0.0, description="Execution engine runtime in milliseconds")
    shared_hit_blocks: int = Field(default=0, description="Buffer pool cache hits")
    shared_read_blocks: int = Field(default=0, description="Physical disk I/O block reads")
    warnings: list[str] = Field(default_factory=list, description="Architectural warnings (e.g. Seq Scan on large table)")
    nodes: list[QueryPlanNode] = Field(default_factory=list, description="Flattened execution plan nodes")
    raw_plan: Any = Field(None, description="Original planner JSON or table representation")


class CatalogSearchByTagResponse(BaseModel):
    """Search response returning matching items alongside query plan diagnostic telemetry."""

    tag: str
    items: list[CatalogItemResponse]
    total: int
    plan_report: QueryPlanReport | None = None


class OffsetComparisonResponse(BaseModel):
    """Response containing items fetched via traditional SQL OFFSET and timing telemetry."""

    items: list[CatalogItemResponse]
    limit: int
    offset: int
    total_returned: int
    execution_time_ms: float
    scan_strategy: str = Field(
        default="OFFSET_SCAN_O_N",
        description="Data scanning archetype demonstrating O(N) degradation on deep pages.",
    )
    plan_report: QueryPlanReport | None = None
