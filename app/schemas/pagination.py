"""Generic Pydantic Schemas for Keyset / Cursor-Based Pagination."""

from __future__ import annotations

from typing import Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field

T = TypeVar("T")


class CursorPageParams(BaseModel):
    """Query parameters for keyset / cursor-based pagination."""

    cursor: str | None = Field(
        default=None,
        description="Opaque URL-safe Base64 cursor token pointing to the last seen item anchor.",
    )
    limit: int = Field(
        default=20,
        ge=1,
        le=100,
        description="Maximum number of records to return per page (bounded between 1 and 100).",
    )


class CursorPageResponse(BaseModel, Generic[T]):
    """Standard generic envelope for cursor-paginated API responses."""

    items: list[T] = Field(
        default_factory=list,
        description="Page slice of returned items.",
    )
    next_cursor: str | None = Field(
        default=None,
        description="Opaque Base64 cursor to pass into next request. None if no further items exist.",
    )
    has_more: bool = Field(
        ...,
        description="Boolean indicator indicating whether subsequent pages are available.",
    )
    total_returned: int = Field(
        ...,
        description="Exact count of items included in current page slice.",
    )

    model_config = ConfigDict(from_attributes=True)
