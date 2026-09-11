"""Enterprise Architecture Compliance Testing & Inward Dependency Enforcement Gate.

Programmatically validates clean architecture layer boundaries, inward dependency rules,
transport layer decoupling, protocol inversion, schema autonomy, and cyclic import prevention
across all application modules using static AST inspection.
"""

from __future__ import annotations

import subprocess  # nosec B404
import sys

import pytest

from app.core.architecture_linter import ArchitectureLinter


@pytest.fixture(scope="module")
def app_linter() -> ArchitectureLinter:
    """Fixture providing an indexed ArchitectureLinter instance for the app/ package."""
    linter = ArchitectureLinter(root_dir="app")
    linter.index()
    return linter


def test_rule_1_inward_layer_boundary_zero_model_leakage(app_linter: ArchitectureLinter) -> None:
    """Rule 1 Invariant: Routers must NEVER import ORM database models from app.models.

    Routers handle HTTP transport, authentication headers, and request/response DTO parsing.
    Direct coupling between routers and database entities breaches the Clean Architecture inward
    dependency boundary and leaks storage schemas to API clients.
    """
    violations = app_linter.check_rule_1_inward_boundary()
    assert len(violations) == 0, (
        f"Inward Boundary Breached: {len(violations)} router(s) directly import database models: {violations}"
    )


def test_rule_2_transport_layer_isolation_zero_fastapi_coupling(app_linter: ArchitectureLinter) -> None:
    """Rule 2 Invariant: Services must NEVER import FastAPI transport objects.

    Services encapsulate domain rules and pure business logic. Importing Request, Response,
    APIRouter, status codes, or HTTPException directly in services violates transport independence
    and prevents running business logic across message brokers, Celery/Arq workers, or CLI tools.
    """
    violations = app_linter.check_rule_2_transport_isolation()
    assert len(violations) == 0, (
        f"Transport Layer Leakage: {len(violations)} service(s) import FastAPI transport objects: {violations}"
    )


def test_rule_3_dependency_inversion_services_inject_protocols(app_linter: ArchitectureLinter) -> None:
    """Rule 3 Invariant: Service constructors inject Protocol interfaces, not concrete repos.

    Adheres strictly to the Dependency Inversion Principle (DIP). Services declare dependencies
    on abstract repository Protocols, allowing persistence engines to be hot-swapped in tests
    with zero coupling to concrete SQLAlchemy or in-memory implementations.
    """
    violations = app_linter.check_rule_3_dependency_inversion()
    assert len(violations) == 0, (
        f"Dependency Inversion Breached: Concrete repositories injected into service(s): {violations}"
    )


def test_rule_4_module_graph_acyclic_invariant_strict_dag(app_linter: ArchitectureLinter) -> None:
    """Rule 4 Invariant: The application module dependency graph G = (V, E) is strictly a DAG.

    Runs Depth-First Search (DFS) three-color cycle detection in O(V + E) time across all
    source files in app/. Asserts exactly 0 circular import cycles exist in the codebase.
    """
    cycles = app_linter.detect_cycles()
    assert len(cycles) == 0, (
        f"Circular Dependency Cycles Detected ({len(cycles)} cycle(s)): {' | '.join(' -> '.join(c) for c in cycles)}"
    )


def test_rule_5_schema_autonomy_dtos_housed_in_schemas_package(app_linter: ArchitectureLinter) -> None:
    """Rule 5 Invariant: Routers and Services must NEVER define Pydantic DTO models inline.

    All request payloads, response projections, and query schemas must strictly reside within
    the app/schemas/ package to guarantee cross-boundary reusability and contract autonomy.
    """
    violations = app_linter.check_rule_5_schema_autonomy()
    assert len(violations) == 0, (
        f"Schema Autonomy Breached: {len(violations)} inline model(s) defined in routers/services: {violations}"
    )


def test_full_architecture_compliance_audit_passes(app_linter: ArchitectureLinter) -> None:
    """Invariant: Consolidated architecture audit across all 5 canonical rules yields ZERO violations."""
    violations = app_linter.check_all()
    assert len(violations) == 0, (
        f"Architecture Compliance Gate Failed with {len(violations)} violation(s): {violations}"
    )


def test_negative_control_detects_intentional_inward_boundary_violation(app_linter: ArchitectureLinter) -> None:
    """Negative Control: Verify ArchitectureLinter detects router importing ORM model."""
    bad_code = """
from fastapi import APIRouter
from app.models.user import UserModel  # FORBIDDEN!

router = APIRouter()
"""
    violations = app_linter.audit_code_snippet(bad_code, file_path="app/routers/bad_router.py")
    assert len(violations) >= 1
    assert any(v.rule_id == "RULE-1-INWARD-BOUNDARY" for v in violations)


def test_negative_control_detects_intentional_transport_isolation_violation(app_linter: ArchitectureLinter) -> None:
    """Negative Control: Verify ArchitectureLinter detects service importing FastAPI Request/Response."""
    bad_code = """
from fastapi import Request, Response  # FORBIDDEN IN SERVICES!

class BillingService:
    def process(self, request: Request) -> Response:
        return Response()
"""
    violations = app_linter.audit_code_snippet(bad_code, file_path="app/services/bad_service.py")
    assert len(violations) >= 1
    assert any(v.rule_id == "RULE-2-TRANSPORT-ISOLATION" for v in violations)


def test_negative_control_detects_intentional_inline_dto_violation(app_linter: ArchitectureLinter) -> None:
    """Negative Control: Verify ArchitectureLinter detects inline BaseModel definition in router."""
    bad_code = """
from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter()

class InlinePaymentPayload(BaseModel):  # FORBIDDEN IN ROUTERS!
    amount: float
"""
    violations = app_linter.audit_code_snippet(bad_code, file_path="app/routers/bad_router.py")
    assert len(violations) >= 1
    assert any(v.rule_id == "RULE-5-SCHEMA-AUTONOMY" for v in violations)


def test_cli_architecture_audit_script_execution() -> None:
    """Invariant: scripts/audit_architecture.py runs via subprocess and returns exit code 0."""
    result = subprocess.run(  # noqa: S603 # nosec B603
        [sys.executable, "scripts/audit_architecture.py", "--target", "app"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, (
        f"Audit script failed with code {result.returncode}:\n{result.stdout}\n{result.stderr}"
    )
    assert "AUDIT PASSED: 100% CLEAN ARCHITECTURE COMPLIANCE VERIFIED" in result.stdout
    assert "Strict DAG (0 Cycles)" in result.stdout
