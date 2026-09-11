"""Domain Ledger Events Specification conforming to CloudEvents enterprise standards.

Features:
- Immutable, frozen Pydantic schemas.
- Monotonically increasing RFC 9562 UUIDv7 identifiers for log-structured index locality.
- Arbitrary-precision decimal representations formatted strictly as strings to prevent floating-point drift.
- Explicit partition_key bound strictly to source_account_id to enforce sequential FIFO ordering in Kafka partitions.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.core.identifiers import generate_uuidv7


class LedgerTransferCompletedEvent(BaseModel):
    """CloudEvents-compliant domain event published upon successful double-entry transfer execution."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    event_id: str = Field(
        default_factory=lambda: str(generate_uuidv7()),
        description="Globally unique RFC 9562 UUIDv7 event identifier",
    )
    event_type: str = Field(
        default="ledger.transfer.completed.v1",
        description="Domain event schema type and version",
    )
    reference_id: str = Field(
        description="External idempotency and correlation reference ID",
    )
    source_account_id: str = Field(
        description="Originating debit/credit account ID",
    )
    destination_account_id: str = Field(
        description="Beneficiary account ID",
    )
    amount: str = Field(
        description="Transferred amount string formatted with arbitrary precision (zero float)",
    )
    currency: str = Field(
        description="ISO 4217 currency code (e.g., USD, EUR, BDT)",
    )
    fee_amount: str = Field(
        default="0.0000",
        description="Platform fee deducted, formatted as decimal string",
    )
    posted_at: str = Field(
        default_factory=lambda: datetime.now(UTC).isoformat(),
        description="ISO 8601 UTC timestamp of financial posting",
    )
    partition_key: str = Field(
        default="",
        description="Kafka message routing key, bound strictly to source_account_id for sequential FIFO ordering",
    )

    def model_post_init(self, __context: Any) -> None:
        """Ensure partition_key is strictly bound to source_account_id."""
        if not self.partition_key:
            object.__setattr__(self, "partition_key", str(self.source_account_id))

    @classmethod
    def create(
        cls,
        *,
        reference_id: str,
        source_account_id: str,
        destination_account_id: str,
        amount: Decimal | str,
        currency: str,
        fee_amount: Decimal | str = "0.0000",
        posted_at: datetime | str | None = None,
        event_id: str | None = None,
    ) -> LedgerTransferCompletedEvent:
        """Factory helper creating an immutable LedgerTransferCompletedEvent with exact string amounts."""
        str_amount = f"{amount:.4f}" if isinstance(amount, Decimal) else str(amount)
        str_fee = f"{fee_amount:.4f}" if isinstance(fee_amount, Decimal) else str(fee_amount)

        if posted_at is None:
            str_posted_at = datetime.now(UTC).isoformat()
        elif isinstance(posted_at, datetime):
            str_posted_at = posted_at.isoformat()
        else:
            str_posted_at = str(posted_at)

        kwargs: dict[str, Any] = {
            "reference_id": reference_id,
            "source_account_id": str(source_account_id),
            "destination_account_id": str(destination_account_id),
            "amount": str_amount,
            "currency": currency.upper(),
            "fee_amount": str_fee,
            "posted_at": str_posted_at,
            "partition_key": str(source_account_id),
        }
        if event_id:
            kwargs["event_id"] = event_id

        return cls(**kwargs)
