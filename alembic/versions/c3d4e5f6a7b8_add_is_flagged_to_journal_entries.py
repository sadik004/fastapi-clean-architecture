"""add_is_flagged_to_journal_entries

Revision ID: c3d4e5f6a7b8
Revises: 8bbc80ff6f79
Create Date: 2026-09-11 10:45:00.000000

"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "c3d4e5f6a7b8"
down_revision: str | Sequence[str] | None = "8bbc80ff6f79"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add is_flagged column to journal_entries table."""
    with op.batch_alter_table("journal_entries", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "is_flagged",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("0"),
                comment="Flag indicating if transaction was flagged by fraud detection engine for compliance review",
            )
        )


def downgrade() -> None:
    """Drop is_flagged column from journal_entries table."""
    with op.batch_alter_table("journal_entries", schema=None) as batch_op:
        batch_op.drop_column("is_flagged")
