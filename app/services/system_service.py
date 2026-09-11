"""System Telemetry Service.

Orchestrates system consolidation metrics, module auditing,
database table registration counting, and curriculum completion attestation.
"""

from __future__ import annotations

import app.models  # noqa: F401 - Ensure all SQLAlchemy models are registered in Base.metadata
from app.core.architecture_linter import ArchitectureLinter
from app.core.database import Base
from app.schemas.system import GraduationSummaryResponseDTO


class SystemService:
    """Service providing executive system telemetry and graduation attestation metrics."""

    @staticmethod
    def get_graduation_summary(routes_count: int) -> GraduationSummaryResponseDTO:
        """Calculate and return system graduation telemetry."""
        # 1. Audit modules dynamically via ArchitectureLinter
        linter = ArchitectureLinter(root_dir="app")
        linter.index()
        modules_count = len(linter.modules)

        # 2. Count active registered SQLAlchemy relational tables
        active_models_count = len(Base.metadata.tables)

        return GraduationSummaryResponseDTO(
            modules_audited=modules_count,
            active_database_models=active_models_count,
            total_registered_endpoints=routes_count,
        )
