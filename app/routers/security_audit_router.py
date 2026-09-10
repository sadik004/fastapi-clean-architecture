"""Observability endpoints for Static Application Security Testing (SAST) & AST Audits."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel, Field

from app.services.security_audit_service import (
    SecurityAuditService,
    get_security_audit_service,
)

router = APIRouter(prefix="/observability/security", tags=["Security Audit & SAST Compliance"])


class SecurityIssueResponse(BaseModel):
    """Pydantic model representing a detected SAST / AST vulnerability."""

    rule_id: str = Field(description="Unique rule or Bandit test identifier")
    severity: str = Field(description="Vulnerability severity: HIGH, MEDIUM, LOW")
    confidence: str = Field(description="Confidence level: HIGH, MEDIUM, LOW")
    cwe: str = Field(description="Common Weakness Enumeration reference ID and title")
    description: str = Field(description="Technical summary of the identified flaw")
    filename: str = Field(description="Path to affected source file")
    line_number: int = Field(description="Source code line number")


class SecurityAuditSummaryResponse(BaseModel):
    """Summary representation of the security audit state."""

    status: str = Field(description="Overall posture: SECURE (0 high, 0 med) or VULNERABLE")
    total_files_scanned: int = Field(description="Number of Python source files scanned")
    total_lines_scanned: int = Field(description="Total lines of code audited")
    high_severity_count: int = Field(description="Count of HIGH severity issues")
    medium_severity_count: int = Field(description="Count of MEDIUM severity issues")
    low_severity_count: int = Field(description="Count of LOW severity issues")
    ast_violations_count: int = Field(description="Count of custom AST architectural rule violations")
    scanned_at: str = Field(description="ISO-8601 timestamp of audit run")


class SecurityAuditDetailsResponse(BaseModel):
    """Comprehensive breakdown of security audit findings and issue catalogue."""

    status: str = Field(description="Overall posture: SECURE or VULNERABLE")
    total_files_scanned: int = Field(description="Number of Python source files scanned")
    total_lines_scanned: int = Field(description="Total lines of code audited")
    high_severity_count: int = Field(description="Count of HIGH severity issues")
    medium_severity_count: int = Field(description="Count of MEDIUM severity issues")
    low_severity_count: int = Field(description="Count of LOW severity issues")
    ast_violations_count: int = Field(description="Count of custom AST architectural rule violations")
    issues: list[SecurityIssueResponse] = Field(description="List of all detected security findings")
    scanned_at: str = Field(description="ISO-8601 timestamp of audit run")


@router.get(
    "/audit-summary",
    response_model=SecurityAuditSummaryResponse,
    status_code=status.HTTP_200_OK,
    summary="Get SAST and AST Security Compliance Summary",
    description="Executes Bandit and AST architectural audits across application code and returns compliance totals.",
)
def get_audit_summary(
    target_dir: Annotated[str, Query(description="Target directory to audit (e.g. app)")] = "app",
    audit_service: SecurityAuditService = Depends(get_security_audit_service),
) -> dict[str, Any]:
    """Execute security scan and return aggregated summary posture."""
    report = audit_service.get_security_summary(target_dir=target_dir)
    return {
        "status": report.status,
        "total_files_scanned": report.total_files_scanned,
        "total_lines_scanned": report.total_lines_scanned,
        "high_severity_count": report.high_severity_count,
        "medium_severity_count": report.medium_severity_count,
        "low_severity_count": report.low_severity_count,
        "ast_violations_count": report.ast_violations_count,
        "scanned_at": report.scanned_at,
    }


@router.get(
    "/audit-details",
    response_model=SecurityAuditDetailsResponse,
    status_code=status.HTTP_200_OK,
    summary="Get Detailed SAST and AST Audit Report",
    description="Returns itemized issues with file locations, CWE classifications, and severity levels.",
)
def get_audit_details(
    target_dir: Annotated[str, Query(description="Target directory to audit (e.g. app)")] = "app",
    audit_service: SecurityAuditService = Depends(get_security_audit_service),
) -> dict[str, Any]:
    """Execute security scan and return full report with itemized findings."""
    report = audit_service.get_security_summary(target_dir=target_dir)
    return report.to_dict()
