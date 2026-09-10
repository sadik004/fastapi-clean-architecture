"""Automated Test Suite for Static Application Security Testing (SAST) & AST Audits."""

from __future__ import annotations

import ast

from fastapi.testclient import TestClient

from app.services.security_audit_service import (
    ASTArchitecturalSecurityVisitor,
    SecurityAuditService,
)


def test_bandit_zero_high_medium_in_app() -> None:
    """Security Invariant: The production app/ directory must contain 0 High and 0 Medium Bandit issues."""
    audit_service = SecurityAuditService()
    issues = audit_service.run_bandit_audit(target_dir="app")

    high_severity = [i for i in issues if i.severity == "HIGH"]
    medium_severity = [i for i in issues if i.severity == "MEDIUM"]

    assert len(high_severity) == 0, f"Detected High severity issues: {high_severity}"
    assert len(medium_severity) == 0, f"Detected Medium severity issues: {medium_severity}"


def test_ast_rule_catches_raw_sql_in_router() -> None:
    """AST Rule 1 Invariant: text() execution in routers violates 3-tier clean architecture (CWE-89)."""
    bad_code = """
from sqlalchemy import text

def get_orders(session):
    return session.execute(text("SELECT * FROM orders WHERE status = 'PAID'"))
"""
    tree = ast.parse(bad_code)
    visitor = ASTArchitecturalSecurityVisitor(filename="app/routers/order_router.py", is_router=True)
    visitor.visit(tree)

    assert len(visitor.issues) == 1
    issue = visitor.issues[0]
    assert issue.rule_id == "fastapi-no-raw-sql-in-routers"
    assert issue.severity == "HIGH"
    assert "CWE-89" in issue.cwe


def test_ast_rule_permits_raw_sql_in_repositories() -> None:
    """AST Rule 1 Invariant: text() is permitted in repository layer when necessary for complex queries."""
    repo_code = """
from sqlalchemy import text

def fetch_aggregated(session):
    return session.execute(text("SELECT COUNT(*) FROM orders"))
"""
    tree = ast.parse(repo_code)
    visitor = ASTArchitecturalSecurityVisitor(
        filename="app/repositories/order_repository.py",
        is_router=False,
    )
    visitor.visit(tree)

    # Should have 0 issues because is_router is False
    assert len(visitor.issues) == 0


def test_ast_rule_catches_insecure_random() -> None:
    """AST Rule 2 Invariant: Standard library 'random' must not be used for cryptographic operations (CWE-338)."""
    bad_code = """
import random

def generate_reset_token():
    return random.randint(100000, 999999)
"""
    tree = ast.parse(bad_code)
    visitor = ASTArchitecturalSecurityVisitor(filename="app/services/auth_service.py", is_router=False)
    visitor.visit(tree)

    assert len(visitor.issues) == 1
    issue = visitor.issues[0]
    assert issue.rule_id == "fastapi-no-insecure-random"
    assert issue.severity == "MEDIUM"
    assert "CWE-338" in issue.cwe


def test_ast_rule_catches_eval_exec() -> None:
    """AST Rule 4 Invariant: eval() and exec() allow remote code execution and are strictly forbidden (CWE-94)."""
    bad_code = """
def execute_expression(expression: str):
    return eval(expression)
"""
    tree = ast.parse(bad_code)
    visitor = ASTArchitecturalSecurityVisitor(filename="app/services/calc_service.py", is_router=False)
    visitor.visit(tree)

    assert len(visitor.issues) == 1
    issue = visitor.issues[0]
    assert issue.rule_id == "fastapi-no-eval-exec"
    assert issue.severity == "HIGH"
    assert "CWE-94" in issue.cwe


def test_ast_rule_catches_print() -> None:
    """AST Rule 3 Invariant: Standard print() leaks unstructured data and is forbidden in production (CWE-778)."""
    bad_code = """
def handle_payment(order_id: str):
    print(f"Processing payment for order {order_id}")
"""
    tree = ast.parse(bad_code)
    visitor = ASTArchitecturalSecurityVisitor(filename="app/services/payment_service.py", is_router=False)
    visitor.visit(tree)

    assert len(visitor.issues) == 1
    issue = visitor.issues[0]
    assert issue.rule_id == "fastapi-no-print-in-production"
    assert issue.severity == "LOW"
    assert "CWE-778" in issue.cwe


def test_ast_rule_catches_hardcoded_secrets() -> None:
    """AST Rule 5 Invariant: Secret identifiers with high-entropy literal assignments must be blocked (CWE-798)."""
    bad_code = """
api_key = "ak_live_99887766554433221100aabbcc"
"""
    tree = ast.parse(bad_code)
    visitor = ASTArchitecturalSecurityVisitor(filename="app/services/vendor_service.py", is_router=False)
    visitor.visit(tree)

    assert len(visitor.issues) == 1
    issue = visitor.issues[0]
    assert issue.rule_id == "fastapi-no-hardcoded-secrets"
    assert issue.severity == "HIGH"
    assert "CWE-798" in issue.cwe


def test_clean_codebase_zero_ast_violations() -> None:
    """Audit Invariant: The entire app/ codebase must have 0 custom AST architectural rule violations."""
    audit_service = SecurityAuditService()
    ast_issues = audit_service.run_ast_audit(target_dir="app")

    assert len(ast_issues) == 0, f"Found unexpected AST violations in app/: {ast_issues}"


def test_security_audit_summary_api(client: TestClient) -> None:
    """Endpoint Invariant: GET /observability/security/audit-summary returns 200 OK with SECURE posture."""
    response = client.get("/observability/security/audit-summary")
    assert response.status_code == 200

    data = response.json()
    assert data["status"] == "SECURE"
    assert data["high_severity_count"] == 0
    assert data["medium_severity_count"] == 0
    assert data["total_files_scanned"] > 50
    assert data["total_lines_scanned"] > 5000
    assert data["ast_violations_count"] == 0
    assert "scanned_at" in data


def test_security_audit_details_api(client: TestClient) -> None:
    """Endpoint Invariant: GET /observability/security/audit-details returns itemized audit findings."""
    response = client.get("/observability/security/audit-details")
    assert response.status_code == 200

    data = response.json()
    assert data["status"] == "SECURE"
    assert "issues" in data
    assert isinstance(data["issues"], list)
    # High and medium counts must be 0
    high_issues = [i for i in data["issues"] if i.get("severity") == "HIGH"]
    medium_issues = [i for i in data["issues"] if i.get("severity") == "MEDIUM"]
    assert len(high_issues) == 0
    assert len(medium_issues) == 0
