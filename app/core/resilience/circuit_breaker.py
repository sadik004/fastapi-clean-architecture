"""Circuit Breaker Pattern Architecture (Three-State Finite State Machine).

Implements the canonical Netflix Hystrix / Martin Fowler Circuit Breaker specification
to prevent cascading failures across downstream services, external APIs, and payment gateways.

States:
- CLOSED: Normal traffic flow. Failures increment counter; trips to OPEN on reaching failure_threshold.
- OPEN: Fail-fast state. Downstream calls are short-circuited; raises CircuitBreakerOpenException.
- HALF_OPEN: Probe state after recovery_timeout expires. Allows trial calls to verify recovery.
  Successful probes reset state to CLOSED; any failure immediately re-trips back to OPEN.

All state checks and transitions execute in strictly O(1) constant time (< 0.05ms).
"""

from __future__ import annotations

import asyncio
import functools
import logging
import time
from collections.abc import Awaitable, Callable
from enum import Enum
from typing import Any, ParamSpec, TypeVar

from app.core.exceptions import CircuitBreakerOpenException

logger = logging.getLogger("app.resilience.circuit_breaker")

P = ParamSpec("P")
R = TypeVar("R")


class CircuitState(str, Enum):
    """Three-State Finite State Machine states for Circuit Breaker."""

    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"


class CircuitBreaker:
    """Enterprise-grade Three-State Circuit Breaker with O(1) State Machine Transitions.

    Attributes:
        name: Identifier for logging and telemetry.
        failure_threshold: Consecutive failures required to trip CLOSED -> OPEN.
        recovery_timeout: Seconds to hold in OPEN before allowing probe in HALF_OPEN.
        half_open_success_threshold: Consecutive successes in HALF_OPEN to reset to CLOSED.
    """

    __slots__ = (
        "_consecutive_failures",
        "_consecutive_successes",
        "_last_failure_time",
        "_last_state_change_time",
        "_lock",
        "_state",
        "_time_provider",
        "_total_calls",
        "_total_failures",
        "_total_short_circuits",
        "_total_successes",
        "failure_threshold",
        "half_open_success_threshold",
        "name",
        "recovery_timeout",
    )

    def __init__(
        self,
        name: str = "default",
        failure_threshold: int = 5,
        recovery_timeout: float = 30.0,
        half_open_success_threshold: int = 2,
        time_provider: Callable[[], float] | None = None,
    ) -> None:
        if failure_threshold < 1:
            raise ValueError("failure_threshold must be at least 1")
        if recovery_timeout <= 0.0:
            raise ValueError("recovery_timeout must be strictly positive")
        if half_open_success_threshold < 1:
            raise ValueError("half_open_success_threshold must be at least 1")

        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.half_open_success_threshold = half_open_success_threshold
        self._time_provider = time_provider or time.monotonic

        now = self._time_provider()
        self._state: CircuitState = CircuitState.CLOSED
        self._consecutive_failures: int = 0
        self._consecutive_successes: int = 0
        self._last_state_change_time: float = now
        self._last_failure_time: float | None = None

        # Telemetry metrics
        self._total_calls: int = 0
        self._total_successes: int = 0
        self._total_failures: int = 0
        self._total_short_circuits: int = 0

        # Lazy async lock
        self._lock: asyncio.Lock | None = None

    @property
    def state(self) -> CircuitState:
        """Return current live circuit state, performing O(1) state evaluation."""
        self._evaluate_state()
        return self._state

    @property
    def consecutive_failures(self) -> int:
        """Return count of consecutive failures recorded."""
        return self._consecutive_failures

    @property
    def consecutive_successes(self) -> int:
        """Return count of consecutive successes in current state."""
        return self._consecutive_successes

    @property
    def last_state_change_time(self) -> float:
        """Monotonic timestamp of the most recent state change."""
        return self._last_state_change_time

    @property
    def remaining_recovery_time(self) -> float:
        """Remaining seconds before an OPEN circuit transitions to HALF_OPEN probe mode."""
        if self._state != CircuitState.OPEN:
            return 0.0
        now = self._time_provider()
        elapsed = now - self._last_state_change_time
        return max(0.0, round(self.recovery_timeout - elapsed, 3))

    def _get_async_lock(self) -> asyncio.Lock:
        """Initialize or retrieve the asyncio.Lock bound to the running event loop."""
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    def _evaluate_state(self) -> None:
        """O(1) Evaluation of OPEN -> HALF_OPEN timeout transition."""
        if self._state == CircuitState.OPEN:
            now = self._time_provider()
            if now - self._last_state_change_time >= self.recovery_timeout:
                logger.info(
                    "Circuit Breaker [%s]: Recovery timeout (%.1fs) elapsed. Transitioning OPEN -> HALF_OPEN.",
                    self.name,
                    self.recovery_timeout,
                )
                self._state = CircuitState.HALF_OPEN
                self._consecutive_failures = 0
                self._consecutive_successes = 0
                self._last_state_change_time = now

    def _before_call(self) -> None:
        """Enforce Fail-Fast semantics prior to executing downstream callable."""
        self._evaluate_state()

        if self._state == CircuitState.OPEN:
            self._total_short_circuits += 1
            remaining = self.remaining_recovery_time
            logger.warning(
                "Circuit Breaker [%s]: Short-circuiting call. State is OPEN (%.1fs remaining).",
                self.name,
                remaining,
            )
            raise CircuitBreakerOpenException(
                message=f"Downstream service is unavailable. Circuit breaker [{self.name}] is OPEN. Please retry later.",
                code="CIRCUIT_BREAKER_OPEN",
                recovery_timeout=self.recovery_timeout,
            )

        self._total_calls += 1

    def _on_success(self) -> None:
        """Update state machine metrics following successful downstream execution."""
        now = self._time_provider()
        self._total_successes += 1

        if self._state == CircuitState.HALF_OPEN:
            self._consecutive_successes += 1
            logger.info(
                "Circuit Breaker [%s]: Probe call succeeded (%d/%d in HALF_OPEN).",
                self.name,
                self._consecutive_successes,
                self.half_open_success_threshold,
            )
            if self._consecutive_successes >= self.half_open_success_threshold:
                logger.info(
                    "Circuit Breaker [%s]: Probe threshold met (%d successes). Resetting HALF_OPEN -> CLOSED.",
                    self.name,
                    self._consecutive_successes,
                )
                self._state = CircuitState.CLOSED
                self._consecutive_failures = 0
                self._consecutive_successes = 0
                self._last_state_change_time = now
        elif self._state == CircuitState.CLOSED:
            self._consecutive_failures = 0

    def _on_failure(self, exc: BaseException) -> None:
        """Update state machine metrics and handle state tripping upon execution failure."""
        now = self._time_provider()
        self._total_failures += 1
        self._last_failure_time = now

        if self._state == CircuitState.HALF_OPEN:
            # Any failure during probe probe immediately re-trips back to OPEN
            logger.warning(
                "Circuit Breaker [%s]: Probe call failed in HALF_OPEN (%s). Immediate re-trip -> OPEN.",
                self.name,
                exc,
            )
            self._state = CircuitState.OPEN
            self._consecutive_failures = 1
            self._consecutive_successes = 0
            self._last_state_change_time = now
        elif self._state == CircuitState.CLOSED:
            self._consecutive_failures += 1
            logger.warning(
                "Circuit Breaker [%s]: Downstream failure recorded (%d/%d failures). Error: %s",
                self.name,
                self._consecutive_failures,
                self.failure_threshold,
                exc,
            )
            if self._consecutive_failures >= self.failure_threshold:
                logger.error(
                    "Circuit Breaker [%s]: Failure threshold reached (%d failures). Tripping CLOSED -> OPEN.",
                    self.name,
                    self._consecutive_failures,
                )
                self._state = CircuitState.OPEN
                self._consecutive_successes = 0
                self._last_state_change_time = now

    async def execute_async(
        self,
        func: Callable[P, Awaitable[R]],
        *args: P.args,
        **kwargs: P.kwargs,
    ) -> R:
        """Wrap an asynchronous coroutine with Circuit Breaker resilience."""
        self._before_call()
        try:
            result = await func(*args, **kwargs)
            self._on_success()
            return result
        except CircuitBreakerOpenException:
            raise
        except BaseException as exc:
            self._on_failure(exc)
            raise

    def execute(
        self,
        func: Callable[P, R],
        *args: P.args,
        **kwargs: P.kwargs,
    ) -> R:
        """Wrap a synchronous callable with Circuit Breaker resilience."""
        self._before_call()
        try:
            result = func(*args, **kwargs)
            self._on_success()
            return result
        except CircuitBreakerOpenException:
            raise
        except BaseException as exc:
            self._on_failure(exc)
            raise

    def decorate(
        self,
        func: Callable[P, Awaitable[R]],
    ) -> Callable[P, Awaitable[R]]:
        """Decorator to wrap async functions with this CircuitBreaker instance."""

        @functools.wraps(func)
        async def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            return await self.execute_async(func, *args, **kwargs)

        return wrapper

    def get_metrics(self) -> dict[str, Any]:
        """Return telemetry snapshot of circuit breaker state and counters."""
        self._evaluate_state()
        return {
            "name": self.name,
            "state": self._state.value,
            "consecutive_failures": self._consecutive_failures,
            "consecutive_successes": self._consecutive_successes,
            "failure_threshold": self.failure_threshold,
            "recovery_timeout": self.recovery_timeout,
            "half_open_success_threshold": self.half_open_success_threshold,
            "remaining_recovery_time_seconds": self.remaining_recovery_time,
            "total_calls": self._total_calls,
            "total_successes": self._total_successes,
            "total_failures": self._total_failures,
            "total_short_circuits": self._total_short_circuits,
        }

    def reset(self) -> None:
        """Reset the circuit breaker to clean initial CLOSED state for test isolation."""
        now = self._time_provider()
        self._state = CircuitState.CLOSED
        self._consecutive_failures = 0
        self._consecutive_successes = 0
        self._last_state_change_time = now
        self._last_failure_time = None
        self._total_calls = 0
        self._total_successes = 0
        self._total_failures = 0
        self._total_short_circuits = 0
