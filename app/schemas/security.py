"""Pydantic DTO Schemas for OWASP Security Hardening (SSRF & Path Traversal)."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class FetchImageRequest(BaseModel):
    """Payload for image proxy fetching."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    image_url: str = Field(..., min_length=1, description="Outbound URL of image to proxy")


class FetchImageResponse(BaseModel):
    """Response returned upon safe SSRF validation and simulated proxy download."""

    model_config = ConfigDict(frozen=True)

    status: str = "success"
    url: str
    message: str = "URL validated and verified safe from SSRF."


class FileDownloadResponse(BaseModel):
    """Response returned upon successful path traversal sanitization."""

    model_config = ConfigDict(frozen=True)

    status: str = "success"
    filename: str
    content: str
    message: str = "Path sanitized and verified safe from directory traversal."
