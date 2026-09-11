"""Pydantic DTO Schemas for Static Application Security Testing (SAST) & AST Audits."""

from __future__ import annotations

from pydantic import BaseModel, Field


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
