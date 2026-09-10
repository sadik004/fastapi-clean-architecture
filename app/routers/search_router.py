"""FastAPI HTTP Router for PostgreSQL Full-Text Search and Trigram Suggestion Engine."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db_session
from app.schemas.search import (
    CreateProductRequest,
    ProductResponse,
    SearchResponse,
    SuggestResponse,
)
from app.services.search_service import SearchService

router = APIRouter(prefix="/search", tags=["Full-Text Search & Fuzzy Discovery"])


@router.post(
    "/products",
    response_model=ProductResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create Searchable Product",
    description="Persists a new product entity and populates full-text search vectors and trigram indexes.",
)
async def create_product_endpoint(
    payload: CreateProductRequest,
    session: AsyncSession = Depends(get_db_session),
) -> ProductResponse:
    """Endpoint for creating a searchable product."""
    product = await SearchService.create_product(session=session, request=payload)
    return ProductResponse.model_validate(product)


@router.get(
    "/products",
    response_model=SearchResponse,
    summary="Search Products (Dual-Mode & Hybrid Degradation)",
    description=(
        "Executes high-precision TSVector stemmed search. If zero results are returned or if fuzzy=True, "
        "automatically degrades to pg_trgm fuzzy typo matching."
    ),
)
async def search_products_endpoint(
    q: str = Query(..., min_length=1, description="Search keyword, phrase, or typo query"),
    fuzzy: bool = Query(default=False, description="Force fuzzy trigram search mode"),
    limit: int = Query(default=20, ge=1, le=100, description="Maximum results to return"),
    threshold: float = Query(default=0.3, ge=0.0, le=1.0, description="Trigram similarity threshold"),
    session: AsyncSession = Depends(get_db_session),
) -> SearchResponse:
    """Dual-mode product search endpoint."""
    return await SearchService.search_products_hybrid(
        session=session,
        query_str=q,
        fuzzy=fuzzy,
        limit=limit,
        similarity_threshold=threshold,
    )


@router.get(
    "/suggest",
    response_model=SuggestResponse,
    summary="Autocomplete Search Suggestions",
    description="Provides real-time query suggestions based on prefix and trigram matching.",
)
async def suggest_endpoint(
    q: str = Query(..., min_length=1, description="Prefix or partial search term"),
    limit: int = Query(default=5, ge=1, le=20, description="Maximum suggestions to return"),
    session: AsyncSession = Depends(get_db_session),
) -> SuggestResponse:
    """Autocomplete suggestions endpoint."""
    suggestions = await SearchService.suggest_autocomplete(
        session=session,
        prefix=q,
        limit=limit,
    )
    return SuggestResponse(query=q, suggestions=suggestions)
