"""create_audit_logs_partitioned_table

Revision ID: a1b2c3d4e5f6
Revises: d95d635922be
Create Date: 2026-09-10 13:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.models.order import GUID

# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f6"
down_revision: str | Sequence[str] | None = "d95d635922be"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade database schema with declarative partitioned audit_logs table."""
    bind = op.get_bind()
    dialect_name = bind.dialect.name

    if dialect_name == "postgresql":
        # Create root partitioned table
        op.execute(
            """
            CREATE TABLE IF NOT EXISTS audit_logs (
                id UUID NOT NULL,
                created_at TIMESTAMPTZ NOT NULL,
                event_type VARCHAR(50) NOT NULL,
                user_id INTEGER NOT NULL,
                payload JSON NOT NULL,
                PRIMARY KEY (id, created_at)
            ) PARTITION BY RANGE (created_at);
            """
        )
        op.execute("CREATE INDEX IF NOT EXISTS ix_audit_logs_event_type ON audit_logs (event_type);")
        op.execute("CREATE INDEX IF NOT EXISTS ix_audit_logs_user_id ON audit_logs (user_id);")
        # Create initial child partition shards
        op.execute(
            """
            CREATE TABLE IF NOT EXISTS audit_logs_y2025 PARTITION OF audit_logs
                FOR VALUES FROM ('2025-01-01 00:00:00+00') TO ('2026-01-01 00:00:00+00');
            """
        )
        op.execute(
            """
            CREATE TABLE IF NOT EXISTS audit_logs_y2026 PARTITION OF audit_logs
                FOR VALUES FROM ('2026-01-01 00:00:00+00') TO ('2027-01-01 00:00:00+00');
            """
        )
        op.execute(
            """
            CREATE TABLE IF NOT EXISTS audit_logs_default PARTITION OF audit_logs DEFAULT;
            """
        )
    else:
        # SQLite dialect fallback table
        inspector = sa.inspect(bind)
        if "audit_logs" not in inspector.get_table_names():
            op.create_table(
                "audit_logs",
                sa.Column("id", GUID(), nullable=False),
                sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
                sa.Column("event_type", sa.String(length=50), nullable=False),
                sa.Column("user_id", sa.Integer(), nullable=False),
                sa.Column("payload", sa.JSON(), nullable=False),
                sa.PrimaryKeyConstraint("id", "created_at"),
            )
            op.create_index("ix_audit_logs_event_type", "audit_logs", ["event_type"], unique=False)
            op.create_index("ix_audit_logs_user_id", "audit_logs", ["user_id"], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    bind = op.get_bind()
    dialect_name = bind.dialect.name

    if dialect_name == "postgresql":
        op.execute("DROP TABLE IF EXISTS audit_logs CASCADE;")
    else:
        inspector = sa.inspect(bind)
        if "audit_logs" in inspector.get_table_names():
            op.drop_table("audit_logs")
