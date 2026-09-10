"""Service demonstrating resilient downstream communication with Exponential Backoff and Jitter."""

from __future__ import annotations

import logging
from typing import Any

from app.core.exceptions import ServiceUnavailableException
from app.core.resilience.backoff import retry_with_backoff

logger = logging.getLogger("app.services.resilient_third_party")


class ResilientThirdPartyService:
    """Service integrating with external third-party dependencies using backoff and jitter."""

    __slots__ = ("_call_counters",)

    def __init__(self) -> None:
        self._call_counters: dict[str, int] = {}

    def reset(self, target_id: str | None = None) -> None:
        """Reset internal simulation counters for test isolation."""
        if target_id is not None:
            self._call_counters.pop(target_id, None)
        else:
            self._call_counters.clear()

    async def execute_simulated_call(
        self,
        target_id: str,
        failures_before_success: int,
        max_retries: int = 3,
        base_delay: float = 0.05,
        max_delay: float = 1.0,
        strategy: str = "full_jitter",
    ) -> dict[str, Any]:
        """Execute a simulated external operation protected by retry_with_backoff.

        Simulates an external downstream dependency (payment gateway, SMS service)
        that temporarily fails `failures_before_success` times before succeeding.
        """
        delays_incurred: list[float] = []

        def on_retry_callback(attempt: int, delay: float, exc: Exception) -> None:
            delays_incurred.append(delay)
            logger.debug(
                "Service retry triggered for %s (attempt %d, delay %.4fs): %s",
                target_id,
                attempt,
                delay,
                exc,
            )

        # Initialize counter if not set
        self._call_counters[target_id] = 0

        async def downstream_call() -> dict[str, Any]:
            self._call_counters[target_id] += 1
            current_call = self._call_counters[target_id]

            if current_call <= failures_before_success:
                raise ServiceUnavailableException(
                    message=(
                        f"Downstream service '{target_id}' failed on attempt "
                        f"{current_call}/{failures_before_success}."
                    ),
                    code="DOWNSTREAM_TEMPORARILY_UNAVAILABLE",
                    retry_after=base_delay,
                )

            return {
                "target_id": target_id,
                "status": "success",
                "message": f"Downstream service call succeeded on attempt {current_call}",
                "completed_call_count": current_call,
            }

        # Dynamically decorate with the requested parameters
        protected_downstream_call = retry_with_backoff(
            max_retries=max_retries,
            base_delay=base_delay,
            max_delay=max_delay,
            strategy=strategy,
            retry_exceptions=(ServiceUnavailableException, TimeoutError),
            on_retry=on_retry_callback,
        )(downstream_call)

        result = await protected_downstream_call()

        attempts_made = self._call_counters[target_id]
        retries_count = len(delays_incurred)
        total_delay = sum(delays_incurred)

        return {
            "target_id": target_id,
            "success": True,
            "attempts_made": attempts_made,
            "retries_count": retries_count,
            "delays": delays_incurred,
            "total_delay_seconds": round(total_delay, 4),
            "result": result,
        }


_global_resilient_third_party_service = ResilientThirdPartyService()


def get_resilient_third_party_service() -> ResilientThirdPartyService:
    """FastAPI dependency provider yielding the shared ResilientThirdPartyService singleton."""
    return _global_resilient_third_party_service


__all__ = ["ResilientThirdPartyService", "get_resilient_third_party_service"]
