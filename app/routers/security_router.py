"""Security Router demonstrating OWASP Top 10 defenses (SSRF and Path Traversal)."""

from __future__ import annotations

from fastapi import APIRouter, Query, status

from app.core.sanitization import sanitize_file_path
from app.core.ssrf_protection import validate_safe_url
from app.schemas.security import (
    FetchImageRequest,
    FetchImageResponse,
    FileDownloadResponse,
)

router = APIRouter(tags=["OWASP Security Hardening"])


@router.post(
    "/proxy/fetch-image",
    response_model=FetchImageResponse,
    status_code=status.HTTP_200_OK,
    summary="Fetch external image through SSRF defense firewall",
)
async def fetch_image(payload: FetchImageRequest) -> FetchImageResponse:
    """Validate external URL against SSRF firewall before dispatching outbound requests.

    Blocks all attempts to access Loopback, RFC 1918 Private networks,
    and Cloud Instance Metadata Services (169.254.169.254).
    """
    safe_url = validate_safe_url(payload.image_url)
    return FetchImageResponse(
        status="success",
        url=safe_url,
        message="URL validated and verified safe from SSRF.",
    )


@router.get(
    "/files/download",
    response_model=FileDownloadResponse,
    status_code=status.HTTP_200_OK,
    summary="Download file with directory traversal neutralization",
)
async def download_file(
    filename: str = Query(..., min_length=1, description="Filename to retrieve"),
) -> FileDownloadResponse:
    """Sanitize and download requested file, neutralizing path traversal vectors.

    Blocks null bytes (\\x00) and directory escapes (../).
    """
    safe_filename = sanitize_file_path(filename, strict=False)
    return FileDownloadResponse(
        status="success",
        filename=safe_filename,
        content=f"Simulated secure file stream for: {safe_filename}",
        message="Path sanitized and verified safe from directory traversal.",
    )
