"""Comprehensive test suite for Day 44: Stateless Authentication Architecture with JWT Lifecycle (HS256 vs RS256) & Redis-Backed Refresh Token Rotation (RTR).

Verifies:
1. Login & Token Generation: Successful authentication returns access token (15m) and refresh token (7d).
2. Stateless Endpoint Verification: Access token accesses /auth/me in O(1) without database lookup.
3. Expired Token Rejection: Expired access tokens are rejected with HTTP 401 Unauthorized.
4. Invalid / Malformed Token Rejection: Bad signatures or wrong token types are rejected with HTTP 401.
5. Legitimate Refresh Token Rotation: Calling /auth/refresh returns new token pair and burns the old token.
6. Stolen Token Replay / Theft Detection: Presenting a burned refresh token triggers theft detection,
   revoking the entire token family in Redis and rejecting with HTTP 401.
7. Revoked Family Invalidation: Once revoked, even the latest refresh token in that family is rejected.
8. Logout Revocation: /auth/logout explicitly purges the token family from Redis.
9. Asymmetric RS256 Lifecycle: Full cryptographic signing and verification using RSA public/private key pairs.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from httpx import ASGITransport, AsyncClient

from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_jwt_token,
)
from app.main import app


@pytest.mark.asyncio
async def test_login_and_token_generation(
    sample_user_payload: dict[str, Any],
    fake_redis: Any,
) -> None:
    """Ensure POST /auth/login returns valid 15-minute access token and 7-day refresh token."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # Seed user
        reg_resp = await ac.post("/users/", json=sample_user_payload)
        assert reg_resp.status_code == 201
        user_id = reg_resp.json()["id"]

        # Login
        login_resp = await ac.post(
            "/auth/login",
            json={
                "username": sample_user_payload["username"],
                "password": sample_user_payload["password"],
            },
        )
        assert login_resp.status_code == 200
        data = login_resp.json()

        assert "access_token" in data
        assert "refresh_token" in data
        assert data["token_type"] == "bearer"
        assert data["expires_in"] == 900

        # Cryptographically verify access token claims
        access_claims = decode_jwt_token(data["access_token"], expected_type="access")
        assert access_claims["sub"] == str(user_id)
        assert access_claims["role"] == "user"
        assert access_claims["token_type"] == "access"

        # Cryptographically verify refresh token claims
        refresh_claims = decode_jwt_token(data["refresh_token"], expected_type="refresh")
        assert refresh_claims["sub"] == str(user_id)
        assert refresh_claims["token_type"] == "refresh"
        assert "family_id" in refresh_claims


@pytest.mark.asyncio
async def test_login_invalid_credentials_rejected(
    sample_user_payload: dict[str, Any],
    fake_redis: Any,
) -> None:
    """Ensure invalid password or non-existent user is rejected with HTTP 401."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        await ac.post("/users/", json=sample_user_payload)

        # Wrong password
        resp = await ac.post(
            "/auth/login",
            json={
                "username": sample_user_payload["username"],
                "password": "WrongPassword!",
            },
        )
        assert resp.status_code == 401

        # Non-existent user
        resp2 = await ac.post(
            "/auth/login",
            json={
                "username": "non_existent_user_999",
                "password": "AnyPassword123!",
            },
        )
        assert resp2.status_code == 401


@pytest.mark.asyncio
async def test_stateless_endpoint_verification_no_db(
    sample_user_payload: dict[str, Any],
    fake_redis: Any,
) -> None:
    """Ensure /auth/me resolves claims statelessly in O(1) time without DB lookup."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        reg_resp = await ac.post("/users/", json=sample_user_payload)
        user_id = reg_resp.json()["id"]

        login_resp = await ac.post(
            "/auth/login",
            json={
                "username": sample_user_payload["username"],
                "password": sample_user_payload["password"],
            },
        )
        access_token = login_resp.json()["access_token"]

        # Valid Bearer token
        me_resp = await ac.get(
            "/auth/me",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert me_resp.status_code == 200
        claims = me_resp.json()
        assert claims["user_id"] == user_id
        assert claims["role"] == "user"

        # Missing header
        no_header_resp = await ac.get("/auth/me")
        assert no_header_resp.status_code == 401
        assert "Missing Authorization header" in no_header_resp.text

        # Invalid scheme
        bad_scheme_resp = await ac.get(
            "/auth/me",
            headers={"Authorization": f"Basic {access_token}"},
        )
        assert bad_scheme_resp.status_code == 401
        assert "Invalid Authorization header scheme" in bad_scheme_resp.text


@pytest.mark.asyncio
async def test_expired_access_token_rejection(
    fake_redis: Any,
) -> None:
    """Ensure expired access token returns HTTP 401 with WWW-Authenticate header."""
    expired_token = create_access_token(
        user_id=100,
        role="user",
        expires_delta=timedelta(seconds=-10),
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get(
            "/auth/me",
            headers={"Authorization": f"Bearer {expired_token}"},
        )
        assert resp.status_code == 401
        assert "Token has expired" in resp.text
        assert resp.headers.get("WWW-Authenticate") == "Bearer"


@pytest.mark.asyncio
async def test_invalid_token_type_or_signature_rejection(
    sample_user_payload: dict[str, Any],
    fake_redis: Any,
) -> None:
    """Ensure refresh tokens or corrupted signatures are rejected on access routes."""
    refresh_tok, _ = create_refresh_token(user_id=10, family_id="fam123")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # Pass refresh token to endpoint expecting access token
        resp = await ac.get(
            "/auth/me",
            headers={"Authorization": f"Bearer {refresh_tok}"},
        )
        assert resp.status_code == 401
        assert "Invalid token type" in resp.text

        # Malformed token
        resp2 = await ac.get(
            "/auth/me",
            headers={"Authorization": "Bearer not.a.valid.jwt.signature"},
        )
        assert resp2.status_code == 401
        assert "Invalid authentication token" in resp2.text


@pytest.mark.asyncio
async def test_refresh_token_rotation_lifecycle_and_theft_detection(
    sample_user_payload: dict[str, Any],
    fake_redis: Any,
) -> None:
    """Ensure legitimate rotation burns old token, and replay triggers theft detection."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # 1. Register & Login
        await ac.post("/users/", json=sample_user_payload)
        login_resp = await ac.post(
            "/auth/login",
            json={
                "username": sample_user_payload["username"],
                "password": sample_user_payload["password"],
            },
        )
        tokens_generation_1 = login_resp.json()
        refresh_token_1 = tokens_generation_1["refresh_token"]
        access_token_1 = tokens_generation_1["access_token"]

        # 2. Legitimate Rotation (Generation 1 -> Generation 2)
        rot_resp_1 = await ac.post(
            "/auth/refresh",
            json={"refresh_token": refresh_token_1},
        )
        assert rot_resp_1.status_code == 200
        tokens_generation_2 = rot_resp_1.json()
        refresh_token_2 = tokens_generation_2["refresh_token"]
        access_token_2 = tokens_generation_2["access_token"]

        assert refresh_token_2 != refresh_token_1
        assert access_token_2 != access_token_1

        # Verify access_token_2 works
        me_resp = await ac.get(
            "/auth/me",
            headers={"Authorization": f"Bearer {access_token_2}"},
        )
        assert me_resp.status_code == 200

        # 3. Theft / Replay Attack Simulation!
        # Attacker tries to present refresh_token_1 (which is now BURNED / CONSUMED!)
        stolen_replay_resp = await ac.post(
            "/auth/refresh",
            json={"refresh_token": refresh_token_1},
        )
        assert stolen_replay_resp.status_code == 401
        assert (
            "Refresh token reuse detected. All sessions revoked for security."
            in stolen_replay_resp.text
        )

        # 4. Invariant: After theft detection, the ENTIRE token family is permanently revoked!
        # Even the legitimate holder of refresh_token_2 is now revoked!
        subsequent_resp = await ac.post(
            "/auth/refresh",
            json={"refresh_token": refresh_token_2},
        )
        assert subsequent_resp.status_code == 401
        assert "Refresh token expired or revoked" in subsequent_resp.text


@pytest.mark.asyncio
async def test_logout_revokes_token_family(
    sample_user_payload: dict[str, Any],
    fake_redis: Any,
) -> None:
    """Ensure /auth/logout immediately revokes the token family in Redis."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        await ac.post("/users/", json=sample_user_payload)
        login_resp = await ac.post(
            "/auth/login",
            json={
                "username": sample_user_payload["username"],
                "password": sample_user_payload["password"],
            },
        )
        refresh_token = login_resp.json()["refresh_token"]

        # Logout
        logout_resp = await ac.post(
            "/auth/logout",
            json={"refresh_token": refresh_token},
        )
        assert logout_resp.status_code == 200
        assert logout_resp.json()["message"] == "Successfully logged out"

        # Subsequent refresh attempt fails
        refresh_resp = await ac.post(
            "/auth/refresh",
            json={"refresh_token": refresh_token},
        )
        assert refresh_resp.status_code == 401
        assert "Refresh token expired or revoked" in refresh_resp.text


@pytest.mark.asyncio
async def test_asymmetric_rs256_cryptographic_lifecycle() -> None:
    """Ensure asymmetric RS256 token signing and public-key verification work correctly."""
    # Generate RSA 2048-bit key pair
    private_key_obj = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = private_key_obj.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("utf-8")

    public_key_obj = private_key_obj.public_key()
    public_pem = public_key_obj.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("utf-8")

    # Sign with private key
    rs256_access_token = create_access_token(
        user_id=777,
        role="admin",
        algorithm="RS256",
        secret_or_private_key=private_pem,
    )

    # Verify with public key
    decoded = decode_jwt_token(
        token=rs256_access_token,
        expected_type="access",
        algorithm="RS256",
        secret_or_public_key=public_pem,
    )
    assert decoded["sub"] == "777"
    assert decoded["role"] == "admin"
    assert decoded["token_type"] == "access"

    # Verify with a different public key -> raises HTTP 401
    other_key_obj = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    other_pub_pem = other_key_obj.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("utf-8")

    with pytest.raises(Exception) as exc_info:
        decode_jwt_token(
            token=rs256_access_token,
            expected_type="access",
            algorithm="RS256",
            secret_or_public_key=other_pub_pem,
        )
    assert "401" in str(exc_info.value)
