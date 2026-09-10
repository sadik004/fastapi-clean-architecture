"""SQLAlchemy 2.0 Declarative ORM Model for Catalog Items with Advanced PostgreSQL Indices.

Demonstrates 4 distinct PostgreSQL indexing archetypes:
1. B-Tree: Standard self-balancing logarithmic tree (Unique SKU & Composite category + price).
2. GIN: Generalized Inverted Index for JSONB document key-value search & array element membership.
3. BRIN: Block Range Index for monotonic time-series data with minimal memory footprint.
4. Hash: O(1) constant-time exact equality lookups for barcode/UUID tokens.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, DateTime, Float, Index, Integer, String
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class CatalogItemModel(Base):
    """High-volume catalog item entity engineered for multi-index search and query plan analysis."""

    __tablename__ = "catalog_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    sku: Mapped[str] = mapped_column(String(50), index=True, unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    category: Mapped[str] = mapped_column(String(100), nullable=False)
    price: Mapped[float] = mapped_column(Float, nullable=False)
    barcode: Mapped[str] = mapped_column(String(100), nullable=False)

    # GIN Index Target 1: JSONB Semi-Structured Attribute Dictionary
    # Fallback to JSON on SQLite for multi-engine/local test compatibility
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB().with_variant(JSON, "sqlite"),
        nullable=False,
        default=dict,
    )

    # GIN Index Target 2: Native PostgreSQL Array of String Tags
    # Fallback to JSON on SQLite for multi-engine/local test compatibility
    tags: Mapped[list[str]] = mapped_column(
        ARRAY(String).with_variant(JSON, "sqlite"),
        nullable=False,
        default=list,
    )

    # BRIN Index Target: Monotonically Appended UTC Timestamp
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )

    __table_args__ = (
        # 1. Composite B-Tree Index: Multi-column prefix scan (category ASC, price ASC)
        Index("ix_catalog_category_price", "category", "price"),
        # 2. Hash Index: O(1) exact equality lookup for barcode
        Index("ix_catalog_barcode_hash", "barcode", postgresql_using="hash"),
        # 3. BRIN Index: Block Range Index for monotonic time-series
        Index("ix_catalog_created_at_brin", "created_at", postgresql_using="brin"),
        # 4. GIN Indexes: Generalized Inverted Index for JSONB containment (@>) and Array containment
        Index("ix_catalog_metadata_gin", "metadata_json", postgresql_using="gin"),
        Index("ix_catalog_tags_gin", "tags", postgresql_using="gin"),
    )
