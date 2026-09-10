"""Resilient Reporting Service with Compartmentalized Bulkhead Isolation."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from typing import Any

from app.core.resilience.bulkhead import Bulkhead


class ResilientReportService:
    """Service demonstrating Bulkhead Compartmentalization.

    Partitions workloads into two isolated execution bulkheads:
    1. heavy_bulkhead: Bounded to max_concurrent=2 for CPU/memory-heavy analytics or PDF export.
    2. light_bulkhead: Bounded to max_concurrent=20 for lightweight, mission-critical operational probes.

    Invariant: 100% saturation of the heavy bulkhead NEVER impacts the light critical bulkhead.
    """

    def __init__(
        self,
        heavy_bulkhead: Bulkhead | None = None,
        light_bulkhead: Bulkhead | None = None,
    ) -> None:
        self._heavy_bulkhead = heavy_bulkhead or Bulkhead(
            name="heavy_reporting",
            max_concurrent=2,
            max_queue=0,
        )
        self._light_bulkhead = light_bulkhead or Bulkhead(
            name="light_critical",
            max_concurrent=20,
            max_queue=0,
        )

    @property
    def heavy_bulkhead(self) -> Bulkhead:
        """Access the heavy reporting bulkhead compartment."""
        return self._heavy_bulkhead

    @property
    def light_bulkhead(self) -> Bulkhead:
        """Access the light critical bulkhead compartment."""
        return self._light_bulkhead

    def reset(self) -> None:
        """Reset internal bulkheads for test isolation."""
        self._heavy_bulkhead.reset()
        self._light_bulkhead.reset()

    async def generate_heavy_report(
        self,
        report_id: str,
        duration_seconds: float = 0.1,
    ) -> dict[str, Any]:
        """Execute a heavy, slow report generation task within the heavy bulkhead.

        If the heavy bulkhead is saturated (2 active executions), this call immediately
        fails-fast with BulkheadFullException without blocking the event loop or other compartments.
        """
        async with self._heavy_bulkhead:
            # Simulate heavy I/O or CPU work
            if duration_seconds > 0.0:
                await asyncio.sleep(duration_seconds)

            job_id = f"job_hvy_{uuid.uuid4().hex[:16]}"
            return {
                "job_id": job_id,
                "report_id": report_id,
                "compartment": self._heavy_bulkhead.name,
                "duration_seconds": duration_seconds,
                "status": "completed",
                "completed_at": datetime.now(UTC),
            }

    async def get_critical_status(self) -> dict[str, Any]:
        """Execute a fast, critical query within the light critical bulkhead compartment."""
        async with self._light_bulkhead:
            job_id = f"job_crit_{uuid.uuid4().hex[:16]}"
            return {
                "job_id": job_id,
                "report_id": "critical_health_probe",
                "compartment": self._light_bulkhead.name,
                "duration_seconds": 0.0,
                "status": "completed",
                "completed_at": datetime.now(UTC),
            }

    def get_metrics(self) -> dict[str, Any]:
        """Return aggregate telemetry metrics across both compartments."""
        return {
            "compartments": {
                self._heavy_bulkhead.name: self._heavy_bulkhead.get_metrics(),
                self._light_bulkhead.name: self._light_bulkhead.get_metrics(),
            }
        }


_global_resilient_report_service = ResilientReportService()


def get_resilient_report_service() -> ResilientReportService:
    """FastAPI dependency provider yielding the shared ResilientReportService singleton."""
    return _global_resilient_report_service
