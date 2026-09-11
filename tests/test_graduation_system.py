"""Test System Consolidation, Telemetry & Graduation Invariants."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.core.architecture_linter import ArchitectureLinter
from app.main import app


@pytest.fixture
def client() -> TestClient:
    """Create test client for application testing."""
    return TestClient(app)


def test_graduation_summary_endpoint(client: TestClient) -> None:
    """Verify GET /api/v1/system/graduation-summary returns complete flight-readiness telemetry."""
    response = client.get("/system/graduation-summary")
    assert response.status_code == 200

    data = response.json()
    assert data["project_name"] == "FastAPI Clean Architecture & Distributed Systems Engine"
    assert data["version"] == "3.0.0-graduation"
    assert data["total_curriculum_days"] == 90
    assert data["modules_audited"] >= 190
    assert data["active_database_models"] >= 8
    assert data["total_registered_endpoints"] >= 30
    assert data["test_suite_status"] == "100% PASSING"
    assert data["architecture_compliance_status"] == "STRICT_DAG_ZERO_CYCLES"
    assert data["security_audit_status"] == "ZERO_HIGH_ZERO_MEDIUM"

    expected_phases = [
        "Phase 1: Foundation",
        "Phase 2: Database",
        "Phase 3: Caching",
        "Phase 4: Security",
        "Phase 5: Messaging",
        "Phase 6: Resilience",
        "Phase 7: DevOps",
        "Phase 8: Fintech Ledger",
    ]
    assert data["phases_completed"] == expected_phases
    assert "timestamp" in data


def test_system_invariants_modules_and_endpoints(client: TestClient) -> None:
    """Verify consistency between system telemetry and dynamic runtime inspect."""
    response = client.get("/system/graduation-summary")
    assert response.status_code == 200
    data = response.json()

    # Route count matches app routes
    assert data["total_registered_endpoints"] == len(app.routes)

    # Modules count matches ArchitectureLinter scan
    linter = ArchitectureLinter(root_dir="app")
    linter.index()
    assert data["modules_audited"] == len(linter.modules)


def test_architecture_linter_zero_cycles() -> None:
    """Verify direct AST architecture analysis detects zero cyclic dependencies."""
    linter = ArchitectureLinter(root_dir="app")
    linter.index()
    cycles = linter.detect_cycles()
    assert len(cycles) == 0, f"Expected 0 circular dependency cycles, found: {cycles}"

    # Verify all 5 canonical architectural rules pass
    rule_1 = linter.check_rule_1_inward_boundary()
    assert len(rule_1) == 0, f"Rule 1 violations: {rule_1}"

    rule_2 = linter.check_rule_2_transport_isolation()
    assert len(rule_2) == 0, f"Rule 2 violations: {rule_2}"

    rule_3 = linter.check_rule_3_dependency_inversion()
    assert len(rule_3) == 0, f"Rule 3 violations: {rule_3}"

    rule_4 = linter.check_rule_4_strict_dag()
    assert len(rule_4) == 0, f"Rule 4 violations: {rule_4}"

    rule_5 = linter.check_rule_5_schema_autonomy()
    assert len(rule_5) == 0, f"Rule 5 violations: {rule_5}"
