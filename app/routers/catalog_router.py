"""API Router for Catalog Search and Database Execution Plan Diagnostics."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import String, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.routing_session import get_primary_session, get_read_session
from app.models.catalog_item import CatalogItemModel
from app.schemas.catalog import (
    CatalogItemCreate,
    CatalogItemResponse,
    CatalogSearchByTagResponse,
    ExplainQueryRequest,
    QueryPlanReport,
)
from app.services.query_plan_service import QueryPlanService

router = APIRouter(prefix="/catalog", tags=["Catalog & Database Index Diagnostics"])


@router.post(
    "/items",
    response_model=CatalogItemResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new catalog item with multi-index attributes",
    description=(
        "Persists a new catalog item across B-Tree (sku, category, price), "
        "GIN (metadata_json, tags), BRIN (created_at), and Hash (barcode) index targets."
    ),
)
async def create_catalog_item_endpoint(
    item_in: CatalogItemCreate,
    primary_session: Annotated[AsyncSession, Depends(get_primary_session)],
) -> CatalogItemResponse:
    """Persist item to primary database engine."""
    new_item = CatalogItemModel(
        sku=item_in.sku,
        name=item_in.name,
        category=item_in.category,
        price=item_in.price,
        barcode=item_in.barcode,
        metadata_json=item_in.metadata_json,
        tags=item_in.tags,
    )
    primary_session.add(new_item)
    await primary_session.commit()
    await primary_session.refresh(new_item)
    return CatalogItemResponse.model_validate(new_item)


@router.get(
    "/search/tags",
    response_model=CatalogSearchByTagResponse,
    status_code=status.HTTP_200_OK,
    summary="Search catalog items by tag with GIN index diagnostic telemetry",
    description=(
        "Queries items matching a specific tag using GIN array/containment index. "
        "Also runs an execution plan diagnostic to analyze optimizer efficiency."
    ),
)
async def search_catalog_by_tags_endpoint(
    tag: Annotated[str, Query(min_length=1, description="Tag to search")],
    read_session: Annotated[AsyncSession, Depends(get_read_session)],
) -> CatalogSearchByTagResponse:
    """Query items by tag on read replica and return execution plan report."""
    bind = read_session.bind
    is_pg = bind is not None and bind.dialect.name == "postgresql"

    if is_pg:
        query = select(CatalogItemModel).where(CatalogItemModel.tags.contains([tag]))
    else:
        query = select(CatalogItemModel).where(CatalogItemModel.tags.cast(String).contains(tag))

    result = await read_session.execute(query)
    items = list(result.scalars().all())

    # Generate diagnostic report for the query pattern
    diagnostic_sql = "SELECT id, sku, name, tags FROM catalog_items WHERE category = :cat"
    plan_report = await QueryPlanService.analyze_query_execution_plan(
        session=read_session,
        sql_query=diagnostic_sql,
        params={"cat": "electronics"},
    )

    return CatalogSearchByTagResponse(
        tag=tag,
        items=[CatalogItemResponse.model_validate(i) for i in items],
        total=len(items),
        plan_report=plan_report,
    )


@router.post(
    "/diagnostics/explain",
    response_model=QueryPlanReport,
    status_code=status.HTTP_200_OK,
    summary="Analyze SQL query execution plan via EXPLAIN (ANALYZE, BUFFERS)",
    description=(
        "Executes a read-only SQL SELECT statement through the query plan engine. "
        "Parses the plan tree, detects scan types (Index Scan vs Seq Scan), extracts buffer pool "
        "hit/read statistics, and flags O(N) sequential scans on large tables."
    ),
)
async def explain_query_endpoint(
    payload: ExplainQueryRequest,
    read_session: Annotated[AsyncSession, Depends(get_read_session)],
) -> QueryPlanReport:
    """Analyze query execution plan safely."""
    return await QueryPlanService.analyze_query_execution_plan(
        session=read_session,
        sql_query=payload.query,
        params=payload.params,
    )
