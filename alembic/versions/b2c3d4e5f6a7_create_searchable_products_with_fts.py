"""create_searchable_products_with_fts

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-09-10 14:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import TSVECTOR

from app.models.order import GUID

# revision identifiers, used by Alembic.
revision: str = "b2c3d4e5f6a7"
down_revision: str | Sequence[str] | None = "a1b2c3d4e5f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade database schema with searchable_products table and FTS/Trigram indexes."""
    bind = op.get_bind()
    dialect_name = bind.dialect.name

    if dialect_name == "postgresql":
        # Enable mandatory PostgreSQL search extensions
        op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm;")
        op.execute("CREATE EXTENSION IF NOT EXISTS btree_gin;")

        op.create_table(
            "searchable_products",
            sa.Column("id", GUID(), nullable=False),
            sa.Column("title", sa.String(length=255), nullable=False),
            sa.Column("description", sa.Text(), nullable=False),
            sa.Column("brand", sa.String(length=100), nullable=False),
            sa.Column("price", sa.Float(), nullable=False),
            sa.Column("search_vector", TSVECTOR(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_searchable_products_title", "searchable_products", ["title"], unique=False)
        op.create_index("ix_searchable_products_brand", "searchable_products", ["brand"], unique=False)
        op.create_index(
            "ix_searchable_products_tsv",
            "searchable_products",
            ["search_vector"],
            unique=False,
            postgresql_using="gin",
        )
        op.create_index(
            "ix_searchable_products_title_trgm",
            "searchable_products",
            ["title"],
            unique=False,
            postgresql_using="gin",
            postgresql_ops={"title": "gin_trgm_ops"},
        )
        op.create_index(
            "ix_searchable_products_brand_trgm",
            "searchable_products",
            ["brand"],
            unique=False,
            postgresql_using="gin",
            postgresql_ops={"brand": "gin_trgm_ops"},
        )
    else:
        # SQLite dialect fallback table
        inspector = sa.inspect(bind)
        if "searchable_products" not in inspector.get_table_names():
            op.create_table(
                "searchable_products",
                sa.Column("id", GUID(), nullable=False),
                sa.Column("title", sa.String(length=255), nullable=False),
                sa.Column("description", sa.Text(), nullable=False),
                sa.Column("brand", sa.String(length=100), nullable=False),
                sa.Column("price", sa.Float(), nullable=False),
                sa.Column("search_vector", sa.Text(), nullable=True),
                sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
                sa.PrimaryKeyConstraint("id"),
            )
            op.create_index("ix_searchable_products_title", "searchable_products", ["title"], unique=False)
            op.create_index("ix_searchable_products_brand", "searchable_products", ["brand"], unique=False)
            op.create_index("ix_searchable_products_tsv", "searchable_products", ["search_vector"], unique=False)
            op.create_index("ix_searchable_products_title_trgm", "searchable_products", ["title"], unique=False)
            op.create_index("ix_searchable_products_brand_trgm", "searchable_products", ["brand"], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "searchable_products" in inspector.get_table_names():
        op.drop_table("searchable_products")
