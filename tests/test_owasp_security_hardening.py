"""Comprehensive test suite for Day 47: OWASP API Security Top 10 Hardening.

Verifies:
1. SSRF Firewall - Cloud Metadata Block: Rejects 169.254.169.254 with HTTP 400 SSRFSecurityException.
2. SSRF Firewall - Loopback and RFC 1918 Private IP Blocks: Rejects 127.0.0.1, 10.0.0.1, 172.16.0.1, 192.168.1.1.
3. SSRF Firewall - Scheme Inspection: Rejects non-HTTP schemes (file://, gopher://, ftp://).
4. SSRF Firewall - Safe Public URL Validation: Accepts and validates safe public targets.
5. Path Traversal Defense & Null Byte Neutralization: Neutralizes directory escapes (../) and rejects null byte injections (\\x00).
6. Strict Production CORS Policy: Whitelisted origins receive CORS headers; unlisted origins are rejected; zero wildcards with credentials.
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.config import get_settings
from app.core.exceptions import PathTraversalException, SSRFSecurityException
from app.core.sanitization import sanitize_file_path
from app.core.ssrf_protection import validate_safe_url
from app.main import app


def test_ssrf_pure_validation_forbidden_ips() -> None:
    """Unit test pure SSRF validation on dangerous and forbidden IP addresses."""
    # 1. Cloud Instance Metadata Service (AWS/GCP/Azure IMDS)
    with pytest.raises(SSRFSecurityException) as exc_imds:
        validate_safe_url("http://169.254.169.254/latest/meta-data/")
    assert "forbidden IP" in str(exc_imds.value)

    # 2. Loopback interfaces (IPv4 and IPv6)
    with pytest.raises(SSRFSecurityException) as exc_loop:
        validate_safe_url("http://127.0.0.1:6379")
    assert "forbidden IP" in str(exc_loop.value)

    with pytest.raises(SSRFSecurityException):
        validate_safe_url("http://localhost:8000")

    # 3. RFC 1918 Private Subnets
    with pytest.raises(SSRFSecurityException):
        validate_safe_url("http://10.0.0.5/internal-api")

    with pytest.raises(SSRFSecurityException):
        validate_safe_url("http://172.16.1.1:5432")

    with pytest.raises(SSRFSecurityException):
        validate_safe_url("http://192.168.1.254/router-login")

    # 4. Prohibited URI schemes
    with pytest.raises(SSRFSecurityException) as exc_scheme:
        validate_safe_url("file:///etc/passwd")
    assert "Prohibited URL scheme" in str(exc_scheme.value)

    with pytest.raises(SSRFSecurityException):
        validate_safe_url("gopher://127.0.0.1:6379/_flushall")

    with pytest.raises(SSRFSecurityException):
        validate_safe_url("ftp://files.example.com/dump.sql")

    # 5. Empty or malformed targets
    with pytest.raises(SSRFSecurityException):
        validate_safe_url("")


def test_path_traversal_pure_sanitization() -> None:
    """Unit test directory traversal neutralization and null byte injection defense."""
    # 1. Null byte injection rejection
    with pytest.raises(PathTraversalException) as exc_null:
        sanitize_file_path("document.pdf\x00.exe")
    assert "Null byte injection" in str(exc_null.value)

    # 2. Control character rejection
    with pytest.raises(PathTraversalException) as exc_ctrl:
        sanitize_file_path("invoice\x07.txt")
    assert "control character" in str(exc_ctrl.value)

    # 3. Directory traversal neutralization (Non-strict mode)
    assert sanitize_file_path("../../../../etc/passwd") == "passwd"
    assert sanitize_file_path("..\\..\\windows\\system32\\cmd.exe") == "cmd.exe"
    assert sanitize_file_path("folder/subfolder/report.pdf") == "report.pdf"
    assert sanitize_file_path("clean_file.txt") == "clean_file.txt"

    # 4. Directory traversal rejection in strict mode
    with pytest.raises(PathTraversalException) as exc_strict:
        sanitize_file_path("../../../../etc/passwd", strict=True)
    assert "Directory traversal sequence detected" in str(exc_strict.value)

    # 5. Empty or purely dots
    with pytest.raises(PathTraversalException):
        sanitize_file_path("...")

    with pytest.raises(PathTraversalException):
        sanitize_file_path("")


@pytest.mark.asyncio
async def test_api_ssrf_cloud_metadata_block() -> None:
    """Ensure POST /proxy/fetch-image strictly rejects cloud metadata targets (HTTP 400)."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        response = await ac.post(
            "/proxy/fetch-image",
            json={"image_url": "http://169.254.169.254/latest/meta-data/"},
        )
        assert response.status_code == 400
        error = response.json()
        assert "forbidden IP" in error["error"]["message"].lower() or "ssrf" in error["error"]["message"].lower()


@pytest.mark.asyncio
async def test_api_ssrf_loopback_and_private_network_block() -> None:
    """Ensure POST /proxy/fetch-image strictly blocks local Redis/database and private subnets."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # Loopback target
        loop_resp = await ac.post(
            "/proxy/fetch-image",
            json={"image_url": "http://127.0.0.1:6379/keys"},
        )
        assert loop_resp.status_code == 400

        # RFC 1918 Private target
        private_resp = await ac.post(
            "/proxy/fetch-image",
            json={"image_url": "http://192.168.0.1/admin"},
        )
        assert private_resp.status_code == 400


@pytest.mark.asyncio
async def test_api_ssrf_safe_public_url() -> None:
    """Ensure POST /proxy/fetch-image successfully validates legitimate public URLs (HTTP 200)."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # Valid public target
        response = await ac.post(
            "/proxy/fetch-image",
            json={"image_url": "https://api.github.com/users"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"
        assert data["url"] == "https://api.github.com/users"
        assert "safe from SSRF" in data["message"]


@pytest.mark.asyncio
async def test_api_path_traversal_neutralization() -> None:
    """Ensure GET /files/download neutralizes ../ traversal and prevents arbitrary file leaks."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # Attempt traversal to /etc/passwd
        response = await ac.get("/files/download", params={"filename": "../../../../etc/passwd"})
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"
        assert data["filename"] == "passwd"
        assert "../" not in data["filename"]
        assert "safe from directory traversal" in data["message"]


@pytest.mark.asyncio
async def test_strict_production_cors_policy() -> None:
    """Verify CORS middleware whitelists trusted origins and rejects unlisted origins."""
    transport = ASGITransport(app=app)
    settings = get_settings()

    # Invariant: Whitelist cannot be wildcard '*' when credentials are true
    assert "*" not in settings.allowed_cors_origins
    assert "http://localhost:3000" in settings.allowed_cors_origins

    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # 1. Preflight from whitelisted origin
        whitelisted_origin = "http://localhost:3000"
        preflight = await ac.options(
            "/health",
            headers={
                "Origin": whitelisted_origin,
                "Access-Control-Request-Method": "GET",
            },
        )
        assert preflight.headers.get("access-control-allow-origin") == whitelisted_origin
        assert preflight.headers.get("access-control-allow-credentials") == "true"

        # 2. Preflight from untrusted origin
        untrusted_origin = "http://malicious-attacker.com"
        untrusted_preflight = await ac.options(
            "/health",
            headers={
                "Origin": untrusted_origin,
                "Access-Control-Request-Method": "GET",
            },
        )
        # Untrusted origin must not be allowed in CORS response header
        assert untrusted_preflight.headers.get("access-control-allow-origin") != untrusted_origin
