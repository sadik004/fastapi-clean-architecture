"""Fintech Double-Entry Ledger Pydantic DTOs & Domain Schemas."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import Enum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class AccountType(str, Enum):
    """Canonical 5-element financial account classification."""

    ASSET = "ASSET"
    LIABILITY = "LIABILITY"
    EQUITY = "EQUITY"
    REVENUE = "REVENUE"
    EXPENSE = "EXPENSE"


class PostingDirection(str, Enum):
    """Movement direction for a ledger posting leg."""

    DEBIT = "DEBIT"
    CREDIT = "CREDIT"


class LedgerAccountCreate(BaseModel):
    """Payload for creating a new ledger account."""

    account_number: str = Field(
        ...,
        min_length=1,
        max_length=64,
        description="Unique business-facing account number",
    )
    name: str = Field(
        ...,
        min_length=1,
        max_length=128,
        description="Human-readable ledger account title",
    )
    account_type: AccountType = Field(
        ...,
        description="Account type determining normal balance calculation",
    )
    currency: str = Field(
        default="USD",
        min_length=3,
        max_length=3,
        description="ISO-4217 3-letter currency code",
    )


class LedgerAccountResponse(BaseModel):
    """Public representation of a ledger account."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    account_number: str
    name: str
    account_type: AccountType
    currency: str
    is_active: bool
    created_at: datetime


class PostingCreateDTO(BaseModel):
    """Individual posting leg within a journal entry."""

    account_id: UUID = Field(
        ...,
        description="Target ledger account ID for this leg",
    )
    amount: Decimal = Field(
        ...,
        gt=Decimal("0.0000"),
        decimal_places=4,
        description="Positive monetary quantity with up to 4 decimal places",
    )
    direction: PostingDirection = Field(
        ...,
        description="Movement direction: DEBIT or CREDIT",
    )


class PostingResponseDTO(BaseModel):
    """Public representation of a committed journal posting leg."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    journal_entry_id: UUID
    account_id: UUID
    amount: Decimal
    direction: PostingDirection
    created_at: datetime


class JournalEntryCreateDTO(BaseModel):
    """Payload for recording an atomic multi-leg journal entry."""

    reference_id: str = Field(
        ...,
        min_length=1,
        max_length=128,
        description="Unique idempotency / business reference identifier",
    )
    description: str = Field(
        ...,
        min_length=1,
        max_length=255,
        description="Audit rationale describing the monetary transfer",
    )
    postings: list[PostingCreateDTO] = Field(
        ...,
        min_length=2,
        description="At least 2 balanced posting legs",
    )


class JournalEntryResponseDTO(BaseModel):
    """Public representation of a committed journal entry with all posting legs."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    reference_id: str
    description: str
    posted_at: datetime
    postings: list[PostingResponseDTO]


class AccountBalanceResponse(BaseModel):
    """Dynamically computed ledger account balance response."""

    account_id: UUID
    account_number: str
    account_name: str
    account_type: AccountType
    currency: str
    total_debits: Decimal
    total_credits: Decimal
    balance: Decimal
    normal_balance: str


class FundTransferRequestDTO(BaseModel):
    """Payload for executing an atomic multi-leg fund transfer."""

    source_account_id: UUID = Field(
        ...,
        description="Originating source account ID to debit/credit funds from",
    )
    destination_account_id: UUID = Field(
        ...,
        description="Target destination account ID to receive transferred funds",
    )
    amount: Decimal = Field(
        ...,
        gt=Decimal("0.0000"),
        decimal_places=4,
        description="Positive monetary quantity to transfer (excluding fees)",
    )
    fee_amount: Decimal = Field(
        default=Decimal("0.0000"),
        ge=Decimal("0.0000"),
        decimal_places=4,
        description="Optional platform processing fee deducted in the same transaction",
    )
    fee_account_id: UUID | None = Field(
        default=None,
        description="Target ledger account to receive platform processing fees (required if fee_amount > 0)",
    )
    reference_id: str = Field(
        ...,
        min_length=1,
        max_length=128,
        description="Unique business transaction / idempotency reference ID",
    )
    description: str = Field(
        ...,
        min_length=1,
        max_length=255,
        description="Audit memo / transfer description",
    )
    exchange_rate: Decimal | None = Field(
        default=None,
        gt=Decimal("0.0000"),
        description="Explicit foreign exchange rate for cross-currency transfers (destination_currency / source_currency)",
    )


class FundTransferResponseDTO(BaseModel):
    """Public representation of an atomically committed fund transfer."""

    model_config = ConfigDict(from_attributes=True)

    journal_entry_id: UUID
    reference_id: str
    transferred_amount: Decimal
    fee_deducted: Decimal
    source_new_balance: Decimal
    destination_new_balance: Decimal
    posted_at: datetime
    source_currency: str = Field(default="USD", description="Currency of source account")
    destination_currency: str = Field(default="USD", description="Currency of destination account")
    exchange_rate: Decimal | None = Field(default=None, description="Applied exchange rate if cross-currency")
    destination_amount: Decimal | None = Field(
        default=None, description="Converted amount received in destination account currency"
    )


class ActiveLockInfo(BaseModel):
    """Telemetry information regarding an active distributed lock."""

    key: str
    resource: str
    ttl_ms: int


class LedgerLockStatusResponse(BaseModel):
    """Operational telemetry envelope for active Redis distributed locks."""

    status: str = "active"
    active_locks_count: int
    locks: list[ActiveLockInfo]
