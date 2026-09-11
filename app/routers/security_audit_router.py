"""Observability endpoints for Static Application Security Testing (SAST) & AST Audits."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, status

from app.schemas.security_audit import (
    SecurityAuditDetailsResponse,
    SecurityAuditSummaryResponse,
)
from app.services.security_audit_service import (
    SecurityAuditService,
    get_security_audit_service,
)

router = APIRouter(prefix="/observability/security", tags=["Security Audit & SAST Compliance"])


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
