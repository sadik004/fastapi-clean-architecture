"""Pydantic v2 Contracts for Full-Text Search, Fuzzy Typo Matching, and Autocomplete Suggestions."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class CreateProductRequest(BaseModel):
    """Payload for creating a new searchable product."""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(..., min_length=1, max_length=255, description="Product title / name")
    description: str = Field(default="", max_length=10000, description="Detailed product description")
    brand: str = Field(..., min_length=1, max_length=100, description="Brand name")
    price: float = Field(..., gt=0, description="Product price in USD")


class ProductResponse(BaseModel):
    """Public product entity representation."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    description: str
    brand: str
    price: float
    created_at: datetime


class SearchResultItem(BaseModel):
    """Single ranked search match item."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    description: str
    brand: str
    price: float
    score: float = Field(..., description="Relevance rank score or trigram similarity coefficient [0.0 - 1.0]")
    match_type: str = Field(..., description="Strategy that found the item: 'FULL_TEXT' or 'FUZZY_TRIGRAM'")


class SearchResponse(BaseModel):
    """Enveloped response for search queries."""

    query: str
    strategy: str = Field(..., description="Search strategy employed: 'FULL_TEXT', 'FUZZY_TRIGRAM', or 'EMPTY'")
    total: int
    results: list[SearchResultItem]


class SuggestResponse(BaseModel):
    """Response payload for autocomplete query suggestions."""

    query: str
    suggestions: list[str]
