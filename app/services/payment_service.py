"""Payment domain service handling charge processing and idempotency side-effect verification."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from app.schemas.payment import PaymentChargeResponse


class PaymentService:
    """Service handling financial charge mutations and telemetry tracking."""

    def __init__(self) -> None:
        self._charge_history: list[dict[str, Any]] = []
        self._processed_counter: int = 0

    @property
    def processed_counter(self) -> int:
        """Return count of actual charge operations executed (to verify zero duplicate side-effects)."""
        return self._processed_counter

    @property
    def charge_history(self) -> list[dict[str, Any]]:
        """Return history of all executed charges."""
        return self._charge_history

    def clear(self) -> None:
        """Reset service state for test isolation."""
        self._charge_history.clear()
        self._processed_counter = 0

    async def process_charge(
        self,
        order_id: str,
        amount: float,
        currency: str = "BDT",
    ) -> PaymentChargeResponse:
        """Execute a payment charge against the simulated payment gateway.

        Every invocation incrementing _processed_counter represents a real financial mutation.
        Under proper idempotency protection, repeated requests MUST NOT increment this counter.
        """
        self._processed_counter += 1
        charge_id = f"ch_{uuid.uuid4().hex[:16]}"
        now = datetime.now(UTC)

        charge_data = {
            "charge_id": charge_id,
            "order_id": order_id,
            "amount": amount,
            "currency": currency,
            "status": "succeeded",
            "created_at": now,
        }
        self._charge_history.append(charge_data)

        return PaymentChargeResponse(
            charge_id=charge_id,
            order_id=order_id,
            amount=amount,
            currency=currency,
            status="succeeded",
            created_at=now,
        )


_global_payment_service = PaymentService()


def get_payment_service() -> PaymentService:
    """FastAPI dependency yielding the shared PaymentService singleton."""
    return _global_payment_service
