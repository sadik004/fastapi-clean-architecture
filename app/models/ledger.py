"""Fintech Double-Entry Ledger Relational Domain Models.

Enforces:
1. Multi-leg append-only immutable journal entries and postings.
2. Dynamic computed balance (Zero mutable balance columns on accounts).
3. Arbitrary precision decimals (Numeric(18, 4)) for absolute financial accuracy.
4. Time-ordered UUIDv7 identifiers for optimal B-Tree index locality.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, DateTime, ForeignKey, Numeric, String
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.core.identifiers import generate_uuidv7
from app.models.order import GUID
from app.schemas.ledger import AccountType, PostingDirection

__all__ = [
    "AccountType",
    "JournalEntryModel",
    "JournalPostingModel",
    "LedgerAccountModel",
    "PostingDirection",
]


class LedgerAccountModel(Base):
    """Ledger account entity representing an internal or customer wallet balance.

    STRICT INVARIANT:
    Never store an editable 'balance' column! Balance must always be dynamically
    computed as the aggregated sum of immutable ledger postings.
    """

    __tablename__ = "ledger_accounts"

    id: Mapped[uuid.UUID] = mapped_column(
        GUID(),
        primary_key=True,
        default=generate_uuidv7,
        comment="Monotonically increasing UUIDv7 identifier",
    )
    account_number: Mapped[str] = mapped_column(
        String(64),
        unique=True,
        index=True,
        nullable=False,
        comment="Unique business-facing account number",
    )
    name: Mapped[str] = mapped_column(
        String(128),
        nullable=False,
        comment="Human-readable ledger account title",
    )
    account_type: Mapped[AccountType] = mapped_column(
        SAEnum(AccountType, native_enum=False, length=20),
        nullable=False,
        comment="Account type determining normal balance calculation",
    )
    currency: Mapped[str] = mapped_column(
        String(3),
        default="USD",
        nullable=False,
        comment="ISO-4217 3-letter currency code",
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
        comment="Flag indicating if account accepts new postings",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=datetime.utcnow,
        nullable=False,
        comment="Timestamp when ledger account was created",
    )

    # Relationship to ledger postings
    postings: Mapped[list[JournalPostingModel]] = relationship(
        "JournalPostingModel",
        back_populates="account",
        lazy="selectin",
    )


class JournalEntryModel(Base):
    """Header record representing an immutable business event or transaction."""

    __tablename__ = "journal_entries"

    id: Mapped[uuid.UUID] = mapped_column(
        GUID(),
        primary_key=True,
        default=generate_uuidv7,
        comment="Monotonically increasing UUIDv7 identifier",
    )
    reference_id: Mapped[str] = mapped_column(
        String(128),
        unique=True,
        index=True,
        nullable=False,
        comment="Unique idempotency / business reference identifier",
    )
    description: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        comment="Audit rationale describing the monetary transfer",
    )
    posted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=datetime.utcnow,
        nullable=False,
        comment="UTC timestamp when transaction was committed",
    )
    is_flagged: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
        comment="Flag indicating if transaction was flagged by fraud detection engine for compliance review",
    )

    # Immutable posting legs comprising the double-entry transaction
    postings: Mapped[list[JournalPostingModel]] = relationship(
        "JournalPostingModel",
        back_populates="journal_entry",
        cascade="all, delete-orphan",
        lazy="selectin",
    )


class JournalPostingModel(Base):
    """Atomic debit or credit leg of a double-entry journal entry."""

    __tablename__ = "journal_postings"

    id: Mapped[uuid.UUID] = mapped_column(
        GUID(),
        primary_key=True,
        default=generate_uuidv7,
        comment="Monotonically increasing UUIDv7 identifier",
    )
    journal_entry_id: Mapped[uuid.UUID] = mapped_column(
        GUID(),
        ForeignKey("journal_entries.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="Parent journal entry header ID",
    )
    account_id: Mapped[uuid.UUID] = mapped_column(
        GUID(),
        ForeignKey("ledger_accounts.id"),
        nullable=False,
        index=True,
        comment="Target ledger account ID for this leg",
    )
    amount: Mapped[Decimal] = mapped_column(
        Numeric(precision=18, scale=4),
        nullable=False,
        comment="Positive monetary quantity with 4 decimal places (Never float!)",
    )
    direction: Mapped[str] = mapped_column(
        String(6),
        nullable=False,
        comment="Movement direction: DEBIT or CREDIT",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=datetime.utcnow,
        nullable=False,
        comment="Timestamp when posting leg was inserted",
    )

    # Relationships
    journal_entry: Mapped[JournalEntryModel] = relationship(
        "JournalEntryModel",
        back_populates="postings",
    )
    account: Mapped[LedgerAccountModel] = relationship(
        "LedgerAccountModel",
        back_populates="postings",
    )
