"""Document Pydantic DTOs for ABAC demonstration."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class DocumentCreateRequest(BaseModel):
    """Schema for creating a new Document."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    title: str = Field(..., min_length=1, max_length=255, description="Document title")
    content: str = Field(..., min_length=1, description="Document body content")
    department: str = Field(..., min_length=1, max_length=100, description="Department name")
    tenant_id: str = Field(..., min_length=1, max_length=100, description="Tenant identifier")
    amount: float = Field(default=0.0, ge=0.0, description="Associated transaction or document value")


class DocumentUpdateRequest(BaseModel):
    """Schema for updating an existing Document."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    title: str | None = Field(default=None, min_length=1, max_length=255)
    content: str | None = Field(default=None, min_length=1)
    status: str | None = Field(default=None, pattern="^(active|archived|approved)$")


class DocumentResponse(BaseModel):
    """Schema representing projected Document entity."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    content: str
    owner_id: int
    tenant_id: str
    department: str
    status: str
    amount: float


class DocumentApproveResponse(BaseModel):
    """Schema returned upon successful document approval."""

    model_config = ConfigDict(frozen=True)

    id: int
    status: str
    approved_by: int
    message: str
