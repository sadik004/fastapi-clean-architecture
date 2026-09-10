"""Resilient Payment Service wrapped with Circuit Breaker FSM protection."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from app.core.resilience.circuit_breaker import CircuitBreaker


class ResilientPaymentService:
    """External payment integration protected by Circuit Breaker pattern.

    Isolates external payment gateway failures (timeouts, HTTP 500s) to prevent
    cascading failures and thread pool starvation in the FastAPI application.
    """

    def __init__(
        self,
        circuit_breaker: CircuitBreaker | None = None,
    ) -> None:
        self._circuit_breaker = circuit_breaker or CircuitBreaker(
            name="payment_gateway",
            failure_threshold=5,
            recovery_timeout=30.0,
            half_open_success_threshold=2,
        )
        self._downstream_calls_executed: int = 0

    @property
    def circuit_breaker(self) -> CircuitBreaker:
        """Access the underlying CircuitBreaker instance for metrics and inspection."""
        return self._circuit_breaker

    @property
    def downstream_calls_executed(self) -> int:
        """Number of calls that reached the actual downstream gateway (not short-circuited)."""
        return self._downstream_calls_executed

    def reset(self) -> None:
        """Reset service counters and circuit breaker state for test isolation."""
        self._circuit_breaker.reset()
        self._downstream_calls_executed = 0

    async def _raw_external_gateway_call(
        self,
        amount: float,
        currency: str,
        should_fail: bool,
    ) -> dict[str, Any]:
        """Simulate external payment gateway HTTP communication."""
        self._downstream_calls_executed += 1

        if should_fail:
            # Simulate downstream HTTP 500 or network timeout
            raise RuntimeError(
                f"External payment gateway connection failure: 504 Gateway Timeout for amount {amount} {currency}"
            )

        now = datetime.now(UTC)
        txn_id = f"txn_ext_{uuid.uuid4().hex[:16]}"
        return {
            "transaction_id": txn_id,
            "amount": amount,
            "currency": currency,
            "status": "succeeded",
            "circuit_state": self._circuit_breaker.state.value,
            "created_at": now,
        }

    async def execute_external_charge(
        self,
        amount: float,
        currency: str = "BDT",
        should_fail: bool = False,
    ) -> dict[str, Any]:
        """Execute charge through the Circuit Breaker FSM engine.

        If the circuit breaker is OPEN, this call will fail-fast with CircuitBreakerOpenException
        without invoking _raw_external_gateway_call.
        """
        return await self._circuit_breaker.execute_async(
            self._raw_external_gateway_call,
            amount=amount,
            currency=currency,
            should_fail=should_fail,
        )


_global_resilient_payment_service = ResilientPaymentService()


def get_resilient_payment_service() -> ResilientPaymentService:
    """FastAPI dependency provider yielding the shared ResilientPaymentService singleton."""
    return _global_resilient_payment_service
