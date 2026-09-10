"""Static Application Security Testing (SAST) & AST Security Analysis Service.

Provides programmatic AST inspection, Bandit SAST scanner execution, and custom architectural
rule enforcement across the codebase in O(N_nodes) time complexity:
1. No Raw SQL in Routers (CWE-89: Enforces 3-tier clean architecture repository boundary).
2. No Insecure PRNG (CWE-338: Enforces Python 'secrets' module over 'random').
3. No Print Statements in Production (CWE-778: Enforces 'structlog' JSON logging).
4. No Dangerous Code Execution (CWE-94/CWE-502: Blocks eval, exec, and unsafe deserialization).
5. No Hardcoded Secrets (CWE-798: Blocks hardcoded credentials outside pydantic-settings).
"""

from __future__ import annotations

import ast
import json
import re
import subprocess  # nosec B404
import sys
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.core.logging import get_logger

logger = get_logger("app.security_audit")

_SECRET_VARIABLE_REGEX = re.compile(
    r"^(api_key|secret_key|jwt_secret|private_key|auth_token|database_password)$",
    re.IGNORECASE,
)


@dataclass(slots=True, frozen=True)
class SecurityIssue:
    """Immutable value object representing an identified security or architectural issue."""

    rule_id: str
    severity: str  # "HIGH", "MEDIUM", "LOW"
    confidence: str  # "HIGH", "MEDIUM", "LOW"
    cwe: str
    description: str
    filename: str
    line_number: int

    def to_dict(self) -> dict[str, Any]:
        """Serialize issue to dictionary representation."""
        return asdict(self)


@dataclass(slots=True, frozen=True)
class SecurityAuditReport:
    """Immutable value object representing an aggregated SAST and AST audit report."""

    status: str  # "SECURE" | "VULNERABLE"
    total_files_scanned: int
    total_lines_scanned: int
    high_severity_count: int
    medium_severity_count: int
    low_severity_count: int
    ast_violations_count: int
    issues: list[SecurityIssue]
    scanned_at: str

    def to_dict(self) -> dict[str, Any]:
        """Serialize report to JSON-compatible dictionary."""
        return {
            "status": self.status,
            "total_files_scanned": self.total_files_scanned,
            "total_lines_scanned": self.total_lines_scanned,
            "high_severity_count": self.high_severity_count,
            "medium_severity_count": self.medium_severity_count,
            "low_severity_count": self.low_severity_count,
            "ast_violations_count": self.ast_violations_count,
            "issues": [issue.to_dict() for issue in self.issues],
            "scanned_at": self.scanned_at,
        }


class ASTArchitecturalSecurityVisitor(ast.NodeVisitor):
    """Abstract Syntax Tree visitor scanning Python source nodes in O(N_nodes) time."""

    def __init__(self, filename: str, is_router: bool = False) -> None:
        self.filename: str = filename
        self.is_router: bool = is_router
        self.issues: list[SecurityIssue] = []

    def visit_Call(self, node: ast.Call) -> None:  # noqa: N802
        # Rule 4: Detect dangerous code execution (eval, exec)
        if isinstance(node.func, ast.Name) and node.func.id in ("eval", "exec"):
            self.issues.append(
                SecurityIssue(
                    rule_id="fastapi-no-eval-exec",
                    severity="HIGH",
                    confidence="HIGH",
                    cwe="CWE-94: Improper Control of Generation of Code ('Code Injection')",
                    description=f"Use of dangerous built-in '{node.func.id}()' allows arbitrary code execution.",
                    filename=self.filename,
                    line_number=node.lineno,
                )
            )

        # Rule 3: Detect print() statements in production code
        elif isinstance(node.func, ast.Name) and node.func.id == "print":
            self.issues.append(
                SecurityIssue(
                    rule_id="fastapi-no-print-in-production",
                    severity="LOW",
                    confidence="HIGH",
                    cwe="CWE-778: Insufficient Logging",
                    description="Direct print() statement detected. Use structlog structured logging instead.",
                    filename=self.filename,
                    line_number=node.lineno,
                )
            )

        elif isinstance(node.func, ast.Attribute):
            # Rule 2: Insecure pseudorandom number generator (random.random, random.choice, etc.)
            if isinstance(node.func.value, ast.Name) and node.func.value.id == "random":
                if not self.filename.endswith("backoff.py"):
                    if node.func.attr in ("random", "choice", "randint", "randrange", "uniform"):
                        self.issues.append(
                            SecurityIssue(
                                rule_id="fastapi-no-insecure-random",
                                severity="MEDIUM",
                                confidence="HIGH",
                                cwe="CWE-338: Use of Cryptographically Weak Pseudo-Random Number Generator (PRNG)",
                                description=(
                                    f"Insecure PRNG 'random.{node.func.attr}()' detected. "
                                    "Use standard library 'secrets' module for cryptographically secure values."
                                ),
                                filename=self.filename,
                                line_number=node.lineno,
                            )
                        )

            # Rule 1: No raw SQL in routers (session.execute(text(...)))
            if self.is_router and node.func.attr == "execute":
                for arg in node.args:
                    if (
                        isinstance(arg, ast.Call)
                        and isinstance(arg.func, ast.Name)
                        and arg.func.id == "text"
                    ):
                        self.issues.append(
                            SecurityIssue(
                                rule_id="fastapi-no-raw-sql-in-routers",
                                severity="HIGH",
                                confidence="HIGH",
                                cwe="CWE-89: SQL Injection / Architectural Boundary Violation",
                                description=(
                                    "Direct execute(text(...)) raw SQL invocation inside router. "
                                    "Delegate all database operations to the repository layer."
                                ),
                                filename=self.filename,
                                line_number=node.lineno,
                            )
                        )

            # Rule 4: Unsafe pickle deserialization
            if (
                isinstance(node.func.value, ast.Name)
                and node.func.value.id == "pickle"
                and node.func.attr in ("load", "loads")
            ):
                self.issues.append(
                    SecurityIssue(
                        rule_id="fastapi-no-unsafe-pickle",
                        severity="HIGH",
                        confidence="HIGH",
                        cwe="CWE-502: Deserialization of Untrusted Data",
                        description="Unsafe pickle.load/loads call detected. Never deserialize untrusted payloads.",
                        filename=self.filename,
                        line_number=node.lineno,
                    )
                )

        self.generic_visit(node)

    def visit_Assign(self, node: ast.Assign) -> None:  # noqa: N802
        # Rule 5: Hardcoded secrets detection in variable assignment
        # Skip app/core/config.py where default values in BaseSettings classes are defined
        if "config.py" not in self.filename:
            for target in node.targets:
                if isinstance(target, ast.Name) and _SECRET_VARIABLE_REGEX.match(target.id):
                    # Check if assigned value is a literal string of length >= 8
                    if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                        val = node.value.value.strip()
                        if len(val) >= 8 and not val.startswith("${"):
                            self.issues.append(
                                SecurityIssue(
                                    rule_id="fastapi-no-hardcoded-secrets",
                                    severity="HIGH",
                                    confidence="HIGH",
                                    cwe="CWE-798: Use of Hard-coded Credentials",
                                    description=(
                                        f"Potential hardcoded credential assigned to '{target.id}'. "
                                        "Use pydantic-settings environment variables via get_settings()."
                                    ),
                                    filename=self.filename,
                                    line_number=node.lineno,
                                )
                            )
        self.generic_visit(node)


class SecurityAuditService:
    """Service orchestrating programmatic SAST scans and AST architectural verification."""

    def __init__(self, root_dir: Path | None = None) -> None:
        self.root_dir: Path = root_dir or Path.cwd()

    def audit_code_snippet(
        self, code: str, filename: str = "snippet.py", is_router: bool = False
    ) -> list[SecurityIssue]:
        """Audit an in-memory Python code string using the AST visitor in O(N_nodes) time."""
        try:
            tree = ast.parse(code, filename=filename)
        except SyntaxError as exc:
            return [
                SecurityIssue(
                    rule_id="syntax-error",
                    severity="HIGH",
                    confidence="HIGH",
                    cwe="CWE-20: Improper Input Validation",
                    description=f"Syntax error in code: {exc}",
                    filename=filename,
                    line_number=exc.lineno or 1,
                )
            ]

        visitor = ASTArchitecturalSecurityVisitor(filename=filename, is_router=is_router)
        visitor.visit(tree)
        return visitor.issues

    def run_ast_audit(self, target_dir: str = "app") -> list[SecurityIssue]:
        """Scan all Python source files in target_dir using AST analysis."""
        target_path = self.root_dir / target_dir
        if not target_path.exists():
            return []

        all_issues: list[SecurityIssue] = []
        for file_path in target_path.rglob("*.py"):
            rel_str = str(file_path.relative_to(self.root_dir)).replace("\\", "/")
            is_router = "routers/" in rel_str

            try:
                content = file_path.read_text(encoding="utf-8")
                issues = self.audit_code_snippet(content, filename=rel_str, is_router=is_router)
                all_issues.extend(issues)
            except Exception as exc:
                logger.warning("ast_scan_error", file=rel_str, error=str(exc))

        return all_issues

    def run_bandit_audit(self, target_dir: str = "app") -> list[SecurityIssue]:
        """Execute Bandit scanner programmatically and extract security issues."""
        target_path = self.root_dir / target_dir
        if not target_path.exists():
            return []

        try:
            cmd = [
                sys.executable,
                "-m",
                "bandit",
                "-q",
                "-r",
                str(target_path),
                "-f",
                "json",
                "-ll",  # Medium and High severity only
            ]
            result = subprocess.run(  # nosec B603 # noqa: S603
                cmd,
                capture_output=True,
                text=True,
                check=False,
            )

            bandit_issues: list[SecurityIssue] = []
            raw_output = result.stdout.strip()
            if raw_output:
                try:
                    if "{" in raw_output:
                        json_str = raw_output[raw_output.index("{") :]
                        data = json.loads(json_str)
                    else:
                        data = json.loads(raw_output)
                    raw_results = data.get("results", [])
                    for item in raw_results:
                        cwe_info = item.get("issue_cwe", {})
                        cwe_str = (
                            f"CWE-{cwe_info.get('id', 'unknown')}: {cwe_info.get('link', '')}"
                            if isinstance(cwe_info, dict)
                            else str(cwe_info)
                        )
                        bandit_issues.append(
                            SecurityIssue(
                                rule_id=item.get("test_id", "bandit-unknown"),
                                severity=item.get("issue_severity", "UNKNOWN"),
                                confidence=item.get("issue_confidence", "UNKNOWN"),
                                cwe=cwe_str,
                                description=item.get("issue_text", ""),
                                filename=item.get("filename", ""),
                                line_number=int(item.get("line_number", 1)),
                            )
                        )
                except json.JSONDecodeError:
                    logger.warning("bandit_json_parse_error", output=result.stdout[:200])

            return bandit_issues
        except Exception as exc:
            logger.warning("bandit_run_failed", error=str(exc))
            return []

    def get_security_summary(self, target_dir: str = "app") -> SecurityAuditReport:
        """Run comprehensive AST and Bandit SAST audit and produce unified compliance report."""
        target_path = self.root_dir / target_dir
        py_files = list(target_path.rglob("*.py")) if target_path.exists() else []

        total_files = len(py_files)
        total_lines = 0
        for f in py_files:
            try:
                total_lines += sum(1 for _ in f.open(encoding="utf-8", errors="ignore"))
            except OSError:
                pass

        ast_issues = self.run_ast_audit(target_dir=target_dir)
        bandit_issues = self.run_bandit_audit(target_dir=target_dir)

        combined_issues = ast_issues + bandit_issues

        high_count = sum(1 for i in combined_issues if i.severity == "HIGH")
        medium_count = sum(1 for i in combined_issues if i.severity == "MEDIUM")
        low_count = sum(1 for i in combined_issues if i.severity == "LOW")

        is_secure = (high_count == 0) and (medium_count == 0)

        return SecurityAuditReport(
            status="SECURE" if is_secure else "VULNERABLE",
            total_files_scanned=total_files,
            total_lines_scanned=total_lines,
            high_severity_count=high_count,
            medium_severity_count=medium_count,
            low_severity_count=low_count,
            ast_violations_count=len(ast_issues),
            issues=combined_issues,
            scanned_at=datetime.now(UTC).isoformat(),
        )


_global_security_audit_service: SecurityAuditService | None = None


def get_security_audit_service() -> SecurityAuditService:
    """Dependency provider returning singleton instance of SecurityAuditService."""
    global _global_security_audit_service
    if _global_security_audit_service is None:
        _global_security_audit_service = SecurityAuditService()
    return _global_security_audit_service
