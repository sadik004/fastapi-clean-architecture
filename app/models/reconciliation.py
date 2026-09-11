"""Relational Domain Models for Ledger Reconciliation and Drift Recovery.

Guarantees:
1. Complete auditability of external payment gateway settlement feeds (Stripe, bKash).
2. Discrepancy classification (MATCHED, MISSING_IN_LEDGER, MISSING_IN_GATEWAY, AMOUNT_MISMATCH).
3. Automated drift recovery tracking via compensating journal entries.
4. Time-ordered UUIDv7 identifiers for monotonic B-Tree clustering.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.core.identifiers import generate_uuidv7
from app.models.order import GUID

__all__ = [
    "ReconciliationBatchModel",
    "ReconciliationItemModel",
]


class ReconciliationBatchModel(Base):
    """Batch header representing an audited external settlement ingestion run."""

    __tablename__ = "reconciliation_batches"

    id: Mapped[uuid.UUID] = mapped_column(
        GUID(),
        primary_key=True,
        default=generate_uuidv7,
        comment="Monotonically increasing UUIDv7 identifier",
    )
    batch_reference: Mapped[str] = mapped_column(
        String(64),
        unique=True,
        index=True,
        nullable=False,
        comment="Unique identifier for the settlement batch feed",
    )
    gateway_name: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        comment="Name of external payment gateway (e.g. STRIPE, BKASH)",
    )
    total_records: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
        comment="Total count of transactions present in the settlement feed",
    )
    matched_records: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
        comment="Count of transactions matching internal ledger records exactly",
    )
    discrepancy_records: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
        comment="Count of transactions with detected discrepancies",
    )
    status: Mapped[str] = mapped_column(
        String(32),
        default="COMPLETED",
        nullable=False,
        comment="Status of reconciliation batch (e.g. COMPLETED, PROCESSING, FAILED)",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
        comment="UTC timestamp when reconciliation batch was processed",
    )

    # 1-to-many relationship with audited reconciliation items
    items: Mapped[list[ReconciliationItemModel]] = relationship(
        "ReconciliationItemModel",
        back_populates="batch",
        cascade="all, delete-orphan",
        lazy="selectin",
    )


class ReconciliationItemModel(Base):
    """Detailed audit record for an individual reconciled transaction."""

    __tablename__ = "reconciliation_items"

    id: Mapped[uuid.UUID] = mapped_column(
        GUID(),
        primary_key=True,
        default=generate_uuidv7,
        comment="Monotonically increasing UUIDv7 identifier",
    )
    batch_id: Mapped[uuid.UUID] = mapped_column(
        GUID(),
        ForeignKey("reconciliation_batches.id"),
        nullable=False,
        index=True,
        comment="Foreign key referencing the parent reconciliation batch",
    )
    reference_id: Mapped[str] = mapped_column(
        String(128),
        index=True,
        nullable=False,
        comment="Transaction reference ID from the external gateway feed",
    )
    external_amount: Mapped[Decimal] = mapped_column(
        Numeric(18, 4),
        nullable=False,
        comment="Transaction amount reported by the external gateway",
    )
    internal_amount: Mapped[Decimal | None] = mapped_column(
        Numeric(18, 4),
        nullable=True,
        comment="Transaction amount recorded in internal double-entry ledger",
    )
    discrepancy_type: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        comment="Discrepancy classification: MATCHED, MISSING_IN_LEDGER, MISSING_IN_GATEWAY, AMOUNT_MISMATCH",
    )
    resolution_status: Mapped[str] = mapped_column(
        String(32),
        default="UNRESOLVED",
        nullable=False,
        comment="Resolution state: UNRESOLVED, AUTO_COMPENSATED, MANUAL_REVIEW, RESOLVED",
    )
    compensating_journal_entry_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(),
        nullable=True,
        comment="Foreign key referencing compensating journal entry if auto-recovered",
    )

    # Relationship back to parent batch
    batch: Mapped[ReconciliationBatchModel] = relationship(
        "ReconciliationBatchModel",
        back_populates="items",
    )
