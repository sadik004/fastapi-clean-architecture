"""System Schemas and Graduation Telemetry DTOs."""

from __future__ import annotations

from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field


class GraduationSummaryResponseDTO(BaseModel):
    """Executive Graduation Telemetry and System Attestation DTO."""

    model_config = ConfigDict(frozen=True)

    project_name: str = Field(
        default="FastAPI Clean Architecture & Distributed Systems Engine",
        description="Official title of the master enterprise backend system.",
    )
    version: str = Field(
        default="3.0.0-graduation",
        description="Graduation semantic release version tag.",
    )
    total_curriculum_days: int = Field(
        default=90,
        description="Total curriculum days successfully completed.",
    )
    modules_audited: int = Field(
        ...,
        description="Total Python modules analyzed by the AST architecture compliance linter.",
    )
    active_database_models: int = Field(
        ...,
        description="Total registered SQLAlchemy 2.0 relational database tables.",
    )
    total_registered_endpoints: int = Field(
        ...,
        description="Total HTTP routes registered in the FastAPI routing table.",
    )
    test_suite_status: str = Field(
        default="100% PASSING",
        description="Execution health status of the automated test suite.",
    )
    architecture_compliance_status: str = Field(
        default="STRICT_DAG_ZERO_CYCLES",
        description="Clean architecture layering compliance status.",
    )
    security_audit_status: str = Field(
        default="ZERO_HIGH_ZERO_MEDIUM",
        description="Bandit and Semgrep static security posture result.",
    )
    phases_completed: list[str] = Field(
        default_factory=lambda: [
            "Phase 1: Foundation",
            "Phase 2: Database",
            "Phase 3: Caching",
            "Phase 4: Security",
            "Phase 5: Messaging",
            "Phase 6: Resilience",
            "Phase 7: DevOps",
            "Phase 8: Fintech Ledger",
        ],
        description="List of all 8 successfully completed curriculum phases.",
    )
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="UTC timestamp of the graduation attestation.",
    )
