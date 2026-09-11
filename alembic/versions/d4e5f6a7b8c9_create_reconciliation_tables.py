"""create_reconciliation_tables

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-09-11 11:00:00.000000

"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

from app.models.order import GUID

# revision identifiers, used by Alembic.
revision: str = "d4e5f6a7b8c9"
down_revision: str | Sequence[str] | None = "c3d4e5f6a7b8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create reconciliation_batches and reconciliation_items tables."""
    op.create_table(
        "reconciliation_batches",
        sa.Column("id", GUID(), nullable=False, comment="Monotonically increasing UUIDv7 identifier"),
        sa.Column(
            "batch_reference",
            sa.String(length=64),
            nullable=False,
            comment="Unique identifier for the settlement batch feed",
        ),
        sa.Column(
            "gateway_name",
            sa.String(length=64),
            nullable=False,
            comment="Name of external payment gateway (e.g. STRIPE, BKASH)",
        ),
        sa.Column(
            "total_records",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
            comment="Total count of transactions present in the settlement feed",
        ),
        sa.Column(
            "matched_records",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
            comment="Count of transactions matching internal ledger records exactly",
        ),
        sa.Column(
            "discrepancy_records",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
            comment="Count of transactions with detected discrepancies",
        ),
        sa.Column(
            "status",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'COMPLETED'"),
            comment="Status of reconciliation batch (e.g. COMPLETED, PROCESSING, FAILED)",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            comment="UTC timestamp when reconciliation batch was processed",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("reconciliation_batches", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_reconciliation_batches_batch_reference"),
            ["batch_reference"],
            unique=True,
        )

    op.create_table(
        "reconciliation_items",
        sa.Column("id", GUID(), nullable=False, comment="Monotonically increasing UUIDv7 identifier"),
        sa.Column(
            "batch_id",
            GUID(),
            nullable=False,
            comment="Foreign key referencing the parent reconciliation batch",
        ),
        sa.Column(
            "reference_id",
            sa.String(length=128),
            nullable=False,
            comment="Transaction reference ID from the external gateway feed",
        ),
        sa.Column(
            "external_amount",
            sa.Numeric(precision=18, scale=4),
            nullable=False,
            comment="Transaction amount reported by the external gateway",
        ),
        sa.Column(
            "internal_amount",
            sa.Numeric(precision=18, scale=4),
            nullable=True,
            comment="Transaction amount recorded in internal double-entry ledger",
        ),
        sa.Column(
            "discrepancy_type",
            sa.String(length=32),
            nullable=False,
            comment="Discrepancy classification: MATCHED, MISSING_IN_LEDGER, MISSING_IN_GATEWAY, AMOUNT_MISMATCH",
        ),
        sa.Column(
            "resolution_status",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'UNRESOLVED'"),
            comment="Resolution state: UNRESOLVED, AUTO_COMPENSATED, MANUAL_REVIEW, RESOLVED",
        ),
        sa.Column(
            "compensating_journal_entry_id",
            GUID(),
            nullable=True,
            comment="Foreign key referencing compensating journal entry if auto-recovered",
        ),
        sa.ForeignKeyConstraint(
            ["batch_id"],
            ["reconciliation_batches.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("reconciliation_items", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_reconciliation_items_batch_id"),
            ["batch_id"],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f("ix_reconciliation_items_reference_id"),
            ["reference_id"],
            unique=False,
        )


def downgrade() -> None:
    """Drop reconciliation_items and reconciliation_batches tables."""
    op.drop_table("reconciliation_items")
    op.drop_table("reconciliation_batches")
