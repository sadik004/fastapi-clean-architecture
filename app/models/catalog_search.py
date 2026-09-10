"""SQLAlchemy 2.0 Declarative ORM Model for Full-Text Search and Trigram Similarity.

Features:
1. Native PostgreSQL TSVector with GIN Inverted Indexing for lexeme matching.
2. Trigram Fuzzy Indexing (pg_trgm) for typo tolerance and autocomplete.
3. Cross-platform compatibility with SQLite variant fallbacks.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import DateTime, Float, Index, String, Text
from sqlalchemy.dialects.postgresql import TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.core.identifiers import generate_uuidv7
from app.models.order import GUID


class SearchableProductModel(Base):
    """Enterprise Catalog Product entity engineered for Native Full-Text Search & Fuzzy Trigram matching.

    Architectural Invariants:
    1. RFC 9562 UUIDv7 Primary Key: Monotonically increasing clustered B-Tree keys.
    2. PostgreSQL TSVector: Normalized lexemes with weights (Title: Weight A, Description: Weight B).
    3. GIN Inverted Indexing: O(log N) term lookups and trigram shingle matching.
    4. SQLite Test Fallback: Gracefully degrades to Text variant for local and test suites.
    """

    __tablename__ = "searchable_products"

    id: Mapped[uuid.UUID] = mapped_column(
        GUID(),
        primary_key=True,
        default=generate_uuidv7,
        doc="Monotonically ordered UUIDv7 primary key.",
    )
    title: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        index=True,
        doc="Product title / name.",
    )
    description: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="",
        doc="Detailed product description.",
    )
    brand: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        index=True,
        doc="Manufacturer or brand name.",
    )
    price: Mapped[float] = mapped_column(
        Float,
        nullable=False,
        doc="Current retail price in USD.",
    )
    search_vector: Mapped[Any] = mapped_column(
        TSVECTOR().with_variant(Text, "sqlite"),
        nullable=True,
        doc="Pre-computed or populated PostgreSQL TSVector column.",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
        doc="Creation timestamp in UTC.",
    )

    __table_args__ = (
        # 1. GIN Inverted Index on TSVector column for full-text phrase/boolean matching
        Index("ix_searchable_products_tsv", "search_vector", postgresql_using="gin"),
        # 2. GIN Trigram Index on title for sub-string and typo-tolerant fuzzy search
        Index(
            "ix_searchable_products_title_trgm",
            "title",
            postgresql_using="gin",
            postgresql_ops={"title": "gin_trgm_ops"},
        ),
        # 3. GIN Trigram Index on brand for quick fuzzy brand filtering
        Index(
            "ix_searchable_products_brand_trgm",
            "brand",
            postgresql_using="gin",
            postgresql_ops={"brand": "gin_trgm_ops"},
        ),
    )

    def __repr__(self) -> str:
        return f"<SearchableProductModel(id={self.id}, title='{self.title}', brand='{self.brand}', price={self.price})>"
