"""Data Transfer Objects and Enums for Ledger Reconciliation & Drift Recovery.

Guarantees:
1. Strict schema validation for external settlement feeds.
2. Domain discrepancy and resolution status enums.
3. Arbitrary precision Decimal financial serialization.
4. Clean Architecture decoupling (zero ORM model leaks).
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from enum import Enum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "DiscrepancyType",
    "ReconciliationBatchRequestDTO",
    "ReconciliationBatchResponseDTO",
    "ReconciliationItemResponseDTO",
    "ReconciliationSummaryDTO",
    "ResolutionStatus",
    "SettlementItemDTO",
]


class DiscrepancyType(str, Enum):
    """Classification of difference between gateway settlement and internal ledger."""

    MATCHED = "MATCHED"
    MISSING_IN_LEDGER = "MISSING_IN_LEDGER"
    MISSING_IN_GATEWAY = "MISSING_IN_GATEWAY"
    AMOUNT_MISMATCH = "AMOUNT_MISMATCH"


class ResolutionStatus(str, Enum):
    """Lifecycle state of discrepancy resolution."""

    UNRESOLVED = "UNRESOLVED"
    AUTO_COMPENSATED = "AUTO_COMPENSATED"
    MANUAL_REVIEW = "MANUAL_REVIEW"
    RESOLVED = "RESOLVED"


class SettlementItemDTO(BaseModel):
    """Single transaction record reported by external payment gateway feed."""

    model_config = ConfigDict(frozen=True)

    reference_id: str = Field(
        ...,
        min_length=1,
        max_length=128,
        description="Unique transaction reference ID from external gateway",
    )
    amount: Decimal = Field(
        ...,
        gt=Decimal("0.0000"),
        description="Transaction amount in gateway currency",
    )
    currency: str = Field(
        default="USD",
        min_length=3,
        max_length=3,
        description="ISO 4217 currency code",
    )
    posted_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="Settlement timestamp reported by external gateway",
    )


class ReconciliationBatchRequestDTO(BaseModel):
    """Payload for initiating an automated settlement feed reconciliation run."""

    model_config = ConfigDict(frozen=True)

    batch_reference: str = Field(
        ...,
        min_length=1,
        max_length=64,
        description="Unique batch identifier for this settlement file / feed",
    )
    gateway_name: str = Field(
        ...,
        min_length=1,
        max_length=64,
        description="Payment gateway name (e.g. STRIPE, BKASH)",
    )
    settlement_items: list[SettlementItemDTO] = Field(
        ...,
        min_length=1,
        description="List of settlement transactions to reconcile against internal ledger",
    )
    auto_compensate: bool = Field(
        default=True,
        description="Whether to automatically post compensating journal entries for MISSING_IN_LEDGER records",
    )


class ReconciliationItemResponseDTO(BaseModel):
    """Detailed reconciliation result for an individual settlement transaction."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID = Field(description="Unique reconciliation item UUID")
    batch_id: UUID = Field(description="Parent reconciliation batch UUID")
    reference_id: str = Field(description="Transaction reference ID")
    external_amount: Decimal = Field(description="Amount from external settlement feed")
    internal_amount: Decimal | None = Field(
        default=None,
        description="Amount from internal ledger journal entry (if found)",
    )
    discrepancy_type: DiscrepancyType = Field(description="Classification of discrepancy")
    resolution_status: ResolutionStatus = Field(description="Resolution status")
    compensating_journal_entry_id: UUID | None = Field(
        default=None,
        description="ID of compensating journal entry if auto-resolved",
    )


class ReconciliationBatchResponseDTO(BaseModel):
    """Summary and item breakdown of an executed reconciliation batch."""

    model_config = ConfigDict(from_attributes=True)

    batch_id: UUID = Field(description="Unique reconciliation batch UUID")
    batch_reference: str = Field(description="Settlement batch reference code")
    gateway_name: str = Field(description="Payment gateway name")
    total_records: int = Field(description="Total transaction records reconciled")
    matched_records: int = Field(description="Transactions exactly matching internal ledger")
    discrepancy_records: int = Field(description="Transactions with detected discrepancies")
    auto_compensated_records: int = Field(description="Transactions auto-compensated via compensating journal entries")
    status: str = Field(description="Batch completion status")
    created_at: datetime = Field(description="Timestamp of reconciliation run")
    items: list[ReconciliationItemResponseDTO] = Field(
        default_factory=list,
        description="Reconciled item details and discrepancy audit records",
    )


# Alias for domain service summary DTO
ReconciliationSummaryDTO = ReconciliationBatchResponseDTO
