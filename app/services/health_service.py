"""Kubernetes Health Probes & Service Lifecycle Diagnostic Service.

Provides segregated probe logic:
1. Startup Probe: Verifies cold-boot initialization and uptime.
2. Liveness Probe: Strictly O(1) in-memory check to prevent the Liveness Trap.
3. Readiness Probe: Timeout-bounded downstream dependency verification (PostgreSQL, Redis).
"""

from __future__ import annotations

import asyncio
import os
import threading
import time
from typing import TYPE_CHECKING, Any

from sqlalchemy import select

if TYPE_CHECKING:
    from redis.asyncio import Redis
    from sqlalchemy.ext.asyncio import AsyncSession


class HealthCheckService:
    """Enterprise health verification service managing Kubernetes probe lifecycles."""

    def __init__(self) -> None:
        self.start_time: float = time.time()
        self._started: bool = True

    def set_started(self, started: bool) -> None:
        """Update application startup status flag."""
        self._started = started

    def is_started(self) -> bool:
        """Check whether application initialization is complete."""
        return self._started

    def check_startup(self) -> tuple[bool, dict[str, Any]]:
        """Verify startup probe state.

        Returns:
            Tuple of (is_started, payload).
        """
        uptime_sec = round(time.time() - self.start_time, 2)
        if not self._started:
            return False, {
                "status": "starting",
                "uptime_seconds": uptime_sec,
                "message": "Application initialization tasks in progress",
            }

        return True, {
            "status": "started",
            "uptime_seconds": uptime_sec,
            "message": "Application successfully initialized",
        }

    def check_liveness(self) -> dict[str, Any]:
        """Execute strictly O(1) in-memory Liveness probe.

        CRITICAL ARCHITECTURAL INVARIANT:
        This method must NEVER make downstream database, cache, or network I/O calls.
        Decoupling Liveness from external dependencies eliminates the Liveness Trap
        (cascading CrashLoopBackOff container restart storms during infrastructure blips).

        Returns:
            Process health telemetry dictionary (< 0.5ms execution).
        """
        return {
            "status": "alive",
            "pid": os.getpid(),
            "active_threads": threading.active_count(),
        }

    async def check_readiness(
        self,
        session: AsyncSession | None = None,
        redis: Redis | None = None,
        timeout_seconds: float = 1.0,
    ) -> tuple[bool, dict[str, Any]]:
        """Execute timeout-bounded Readiness probe against critical downstream dependencies.

        When a dependency fails, returns is_ready=False to signal Kubernetes Service / Ingress
        to temporarily remove the pod from traffic routing WITHOUT restarting the container.

        Args:
            session: Optional async database session.
            redis: Optional async Redis client.
            timeout_seconds: Strict timeout threshold per dependency check (default 1.0s).

        Returns:
            Tuple of (is_ready, details_payload).
        """
        checks: dict[str, str] = {}
        all_healthy = True

        # 1. Database connectivity check
        if session is not None:
            try:
                await asyncio.wait_for(
                    session.scalar(select(1)),
                    timeout=timeout_seconds,
                )
                checks["database"] = "healthy"
            except TimeoutError:
                checks["database"] = "unhealthy: query timed out"
                all_healthy = False
            except Exception as exc:
                checks["database"] = f"unhealthy: {type(exc).__name__}"
                all_healthy = False
        else:
            checks["database"] = "disabled"

        # 2. Redis connectivity check
        if redis is not None:
            try:
                pong = await asyncio.wait_for(
                    redis.ping(),
                    timeout=timeout_seconds,
                )
                if pong:
                    checks["redis"] = "healthy"
                else:
                    checks["redis"] = "unhealthy: ping returned falsy"
                    all_healthy = False
            except TimeoutError:
                checks["redis"] = "unhealthy: ping timed out"
                all_healthy = False
            except Exception as exc:
                checks["redis"] = f"unhealthy: {type(exc).__name__}"
                all_healthy = False
        else:
            checks["redis"] = "disabled"

        payload = {
            "status": "ready" if all_healthy else "unready",
            "checks": checks,
        }
        return all_healthy, payload


# Global singleton instance
_health_service = HealthCheckService()


def get_health_service() -> HealthCheckService:
    """Dependency provider returning singleton HealthCheckService."""
    return _health_service
