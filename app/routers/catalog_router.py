"""API Router for Catalog Search and Database Execution Plan Diagnostics."""

from __future__ import annotations

import time
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.pagination.cursor import CursorCodec
from app.core.routing_session import get_primary_session, get_read_session
from app.repositories.catalog_repository import (
    CatalogRepositoryProtocol,
    SqlAlchemyCatalogRepository,
)
from app.schemas.catalog import (
    CatalogItemCreate,
    CatalogItemResponse,
    CatalogSearchByTagResponse,
    ExplainQueryRequest,
    OffsetComparisonResponse,
    QueryPlanReport,
)
from app.schemas.pagination import CursorPageResponse
from app.services.query_plan_service import QueryPlanService

router = APIRouter(prefix="/catalog", tags=["Catalog & Database Index Diagnostics"])


def get_catalog_repository() -> CatalogRepositoryProtocol:
    """Dependency provider returning CatalogRepository implementation."""
    return SqlAlchemyCatalogRepository()


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
    catalog_repo: Annotated[CatalogRepositoryProtocol, Depends(get_catalog_repository)],
) -> CatalogItemResponse:
    """Persist item to primary database engine."""
    new_item = await catalog_repo.create_item(
        session=primary_session,
        sku=item_in.sku,
        name=item_in.name,
        category=item_in.category,
        price=item_in.price,
        barcode=item_in.barcode,
        metadata_json=item_in.metadata_json,
        tags=item_in.tags,
    )
    return CatalogItemResponse.model_validate(new_item)


@router.get(
    "/items/keyset",
    response_model=CursorPageResponse[CatalogItemResponse],
    status_code=status.HTTP_200_OK,
    summary="Keyset / Cursor-based paginated catalog items",
    description=(
        "Retrieves a page of catalog items using deterministic composite tuple keyset seeking "
        "(created_at DESC, id DESC). Executes in O(1) B-Tree seek time regardless of pagination depth."
    ),
)
async def get_catalog_items_keyset_endpoint(
    read_session: Annotated[AsyncSession, Depends(get_read_session)],
    catalog_repo: Annotated[CatalogRepositoryProtocol, Depends(get_catalog_repository)],
    cursor: Annotated[str | None, Query(description="Opaque Base64 cursor token")] = None,
    limit: Annotated[int, Query(ge=1, le=100, description="Page limit (1-100)")] = 20,
) -> CursorPageResponse[CatalogItemResponse]:
    """Execute O(1) B-Tree seek keyset pagination."""
    cursor_data = None
    if cursor is not None:
        cursor_data = CursorCodec.decode_cursor(cursor)

    items, has_more, next_cursor = await catalog_repo.get_paginated_items_keyset(
        session=read_session,
        limit=limit,
        cursor_data=cursor_data,
    )

    return CursorPageResponse(
        items=[CatalogItemResponse.model_validate(i) for i in items],
        next_cursor=next_cursor,
        has_more=has_more,
        total_returned=len(items),
    )


@router.get(
    "/items/offset-comparison",
    response_model=OffsetComparisonResponse,
    status_code=status.HTTP_200_OK,
    summary="Legacy SQL OFFSET paginated catalog items (Benchmarking Diagnostics)",
    description=(
        "Retrieves a slice of catalog items using traditional SQL LIMIT / OFFSET. "
        "Demonstrates O(N) database scan degradation on deep pages for benchmarking against keyset seeking."
    ),
)
async def get_catalog_items_offset_endpoint(
    read_session: Annotated[AsyncSession, Depends(get_read_session)],
    catalog_repo: Annotated[CatalogRepositoryProtocol, Depends(get_catalog_repository)],
    offset: Annotated[int, Query(ge=0, description="Number of rows to skip")] = 0,
    limit: Annotated[int, Query(ge=1, le=100, description="Page size")] = 20,
) -> OffsetComparisonResponse:
    """Execute traditional O(N) SQL OFFSET query with latency telemetry."""
    start_time = time.perf_counter()
    items = await catalog_repo.get_paginated_items_offset(
        session=read_session,
        limit=limit,
        offset=offset,
    )
    elapsed_ms = round((time.perf_counter() - start_time) * 1000, 3)

    return OffsetComparisonResponse(
        items=[CatalogItemResponse.model_validate(i) for i in items],
        limit=limit,
        offset=offset,
        total_returned=len(items),
        execution_time_ms=elapsed_ms,
        scan_strategy="OFFSET_SCAN_O_N",
    )


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
    catalog_repo: Annotated[CatalogRepositoryProtocol, Depends(get_catalog_repository)],
) -> CatalogSearchByTagResponse:
    """Query items by tag on read replica and return execution plan report."""
    items = await catalog_repo.search_by_tag(session=read_session, tag=tag)

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
