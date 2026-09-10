"""Catalog Repository for Keyset / Cursor-Based Pagination and Query Plan Diagnostics.

Layer: Repositories (Data Access Layer)
Constraints:
- Keyset Seek: Instantaneous O(1) B-Tree seeking via composite tuple comparison.
- Tie-Breaker: (created_at DESC, id DESC) ensures deterministic pagination with zero row skipping or duplication.
- Limit + 1 Probe: Detects subsequent page existence without executing an expensive COUNT(*) scan.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol

from sqlalchemy import select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.pagination.cursor import CursorCodec
from app.models.catalog_item import CatalogItemModel


class CatalogRepositoryProtocol(Protocol):
    """Abstract protocol contract for catalog item queries."""

    async def get_paginated_items_keyset(
        self,
        session: AsyncSession,
        limit: int,
        cursor_data: tuple[datetime, Any] | None = None,
    ) -> tuple[list[CatalogItemModel], bool, str | None]:
        """Fetch items using keyset pagination."""
        ...

    async def get_paginated_items_offset(
        self,
        session: AsyncSession,
        limit: int,
        offset: int,
    ) -> list[CatalogItemModel]:
        """Fetch items using legacy SQL offset pagination."""
        ...


class SqlAlchemyCatalogRepository:
    """SQLAlchemy 2.0 implementation of CatalogRepositoryProtocol."""

    async def get_paginated_items_keyset(
        self,
        session: AsyncSession,
        limit: int,
        cursor_data: tuple[datetime, Any] | None = None,
    ) -> tuple[list[CatalogItemModel], bool, str | None]:
        """Retrieve a paginated slice using composite keyset (created_at, id) seeking.

        Complexity: O(limit) B-Tree seek time, strictly independent of pagination depth N.

        Args:
            session: AsyncSession for execution.
            limit: Page size requested by caller.
            cursor_data: Optional tuple of (cursor_created_at, cursor_id) from decoded token.

        Returns:
            Tuple of:
            - items: list of CatalogItemModel of length <= limit.
            - has_more: True if there is a next page, False otherwise.
            - next_cursor: Base64 opaque cursor token for the next page, or None if exhausted.
        """
        query = select(CatalogItemModel)

        if cursor_data is not None:
            cursor_created_at, cursor_id = cursor_data
            # Enforce composite tuple comparison: (created_at, id) < (:cursor_created_at, :cursor_id)
            query = query.where(
                tuple_(CatalogItemModel.created_at, CatalogItemModel.id) < (cursor_created_at, cursor_id)
            )

        # Deterministic order with primary key tie-breaker
        query = (
            query.order_by(CatalogItemModel.created_at.desc(), CatalogItemModel.id.desc())
            .limit(limit + 1)
        )

        result = await session.execute(query)
        fetched_rows = list(result.scalars().all())

        has_more = len(fetched_rows) > limit
        items = fetched_rows[:limit]

        next_cursor: str | None = None
        if has_more and items:
            last_item = items[-1]
            next_cursor = CursorCodec.encode_cursor(last_item.created_at, last_item.id)

        return items, has_more, next_cursor

    async def get_paginated_items_offset(
        self,
        session: AsyncSession,
        limit: int,
        offset: int,
    ) -> list[CatalogItemModel]:
        """Retrieve a slice using traditional SQL LIMIT / OFFSET.

        Complexity: O(N) where N = offset + limit (scans and discards offset rows).

        Args:
            session: AsyncSession for execution.
            limit: Page size.
            offset: Number of rows to skip.

        Returns:
            List of CatalogItemModel.
        """
        query = (
            select(CatalogItemModel)
            .order_by(CatalogItemModel.created_at.desc(), CatalogItemModel.id.desc())
            .limit(limit)
            .offset(offset)
        )
        result = await session.execute(query)
        return list(result.scalars().all())
