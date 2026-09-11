"""Enterprise Graceful Shutdown & SIGTERM Handling Architecture.

Intercepts operating system termination signals (SIGTERM, SIGINT), trips Kubernetes Readiness Probes
to halt ingress routing, cleanly drains in-flight requests within a bounded grace window,
and coordinates clean disposal of connection pools for Zero Dropped Requests during rolling updates.
"""

from __future__ import annotations

import asyncio
import signal
import sys
from typing import Any

from app.core.logging import get_logger

logger = get_logger("app.lifecycle")


class ShutdownManager:
    """Enterprise Graceful Shutdown & Connection Draining Lifecycle Manager.

    Executes the 3-Phase Graceful Shutdown Protocol:
    1. Phase 1: Traffic Cutoff (Trip Readiness Probe to HTTP 503).
    2. Phase 2: In-Flight Connection Draining (wait for in-flight requests == 0, bounded by timeout).
    3. Phase 3: State & Pool Disposal (clean engine disposal, broker flushes, clean exit).
    """

    def __init__(self, shutdown_timeout: float = 30.0) -> None:
        self.is_shutting_down: bool = False
        self.in_flight_requests: int = 0
        self.shutdown_timeout: float = shutdown_timeout
        self._drain_event: asyncio.Event = asyncio.Event()

    def increment_in_flight(self) -> None:
        """Increment active in-flight request counter."""
        self.in_flight_requests += 1

    def decrement_in_flight(self) -> None:
        """Decrement active in-flight request counter and signal drain event if zero."""
        self.in_flight_requests = max(0, self.in_flight_requests - 1)
        if self.in_flight_requests == 0 and self.is_shutting_down:
            self._drain_event.set()

    def initiate_shutdown(self) -> None:
        """Trip Phase 1: Cut off new traffic by setting is_shutting_down = True."""
        if not self.is_shutting_down:
            self.is_shutting_down = True
            logger.info(
                "graceful_shutdown_initiated",
                phase="phase_1_traffic_cutoff",
                in_flight_requests=self.in_flight_requests,
                message="Readiness probe tripped to HTTP 503. Awaiting connection draining.",
            )
            if self.in_flight_requests == 0:
                self._drain_event.set()

    async def wait_for_drain(self, timeout: float | None = None) -> bool:
        """Trip Phase 2: Asynchronously await draining of in-flight requests.

        Uses a non-blocking asyncio polling loop yielding control via await asyncio.sleep(0.1)
        until in_flight_requests == 0 or the bounded timeout expires.
        Returns True if cleanly drained (in_flight == 0), False if timeout elapsed.
        """
        effective_timeout = timeout if timeout is not None else self.shutdown_timeout
        self.initiate_shutdown()

        if self.in_flight_requests == 0:
            logger.info(
                "graceful_shutdown_drained",
                phase="phase_2_complete",
                in_flight_requests=0,
                message="Zero active requests remaining. Ready for pool disposal.",
            )
            return True

        logger.info(
            "graceful_shutdown_draining_active_requests",
            phase="phase_2_draining",
            in_flight_requests=self.in_flight_requests,
            timeout_seconds=effective_timeout,
        )

        loop = asyncio.get_running_loop()
        start_time = loop.time()
        while self.in_flight_requests > 0:
            elapsed = loop.time() - start_time
            if elapsed >= effective_timeout:
                logger.warning(
                    "graceful_shutdown_drain_timeout_exceeded",
                    phase="phase_2_timeout",
                    remaining_in_flight=self.in_flight_requests,
                    timeout_seconds=effective_timeout,
                    message="Forcing continuation to Phase 3 state and pool disposal.",
                )
                return False
            # Yield control to the event loop so active coroutines can process
            sleep_duration = min(0.1, max(0.001, effective_timeout - elapsed))
            await asyncio.sleep(sleep_duration)

        logger.info(
            "graceful_shutdown_drained",
            phase="phase_2_complete",
            in_flight_requests=self.in_flight_requests,
        )
        return True

    def register_signal_handlers(self, loop: asyncio.AbstractEventLoop | None = None) -> None:
        """Register OS signal handlers for SIGTERM and SIGINT with cross-platform resilience."""
        target_loop: asyncio.AbstractEventLoop | None = None
        try:
            target_loop = loop or asyncio.get_running_loop()
        except RuntimeError:
            target_loop = None

        # Try asyncio event loop signal handlers (POSIX platforms)
        if target_loop is not None and sys.platform != "win32":
            try:
                for sig in (signal.SIGTERM, signal.SIGINT):
                    target_loop.add_signal_handler(sig, self.initiate_shutdown)
                logger.info("signal_handlers_registered", mechanism="asyncio_loop", signals=["SIGTERM", "SIGINT"])
                return
            except (NotImplementedError, RuntimeError):
                pass

        # Fallback to standard library signal handlers
        def _sync_signal_handler(signum: int, frame: Any) -> None:
            self.initiate_shutdown()

        try:
            signal.signal(signal.SIGINT, _sync_signal_handler)
            if hasattr(signal, "SIGTERM"):
                signal.signal(signal.SIGTERM, _sync_signal_handler)
            logger.info("signal_handlers_registered", mechanism="std_signal", signals=["SIGINT", "SIGTERM"])
        except (ValueError, AttributeError) as exc:
            logger.warning("signal_handlers_registration_skipped", reason=str(exc))

    def reset(self) -> None:
        """Reset state for clean test isolation."""
        self.is_shutting_down = False
        self.in_flight_requests = 0
        self._drain_event = asyncio.Event()


_global_shutdown_manager: ShutdownManager | None = None


def get_shutdown_manager() -> ShutdownManager:
    """Return the singleton ShutdownManager instance."""
    global _global_shutdown_manager
    if _global_shutdown_manager is None:
        _global_shutdown_manager = ShutdownManager()
    return _global_shutdown_manager
