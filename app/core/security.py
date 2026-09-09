"""Security utilities for memory-hard Argon2id password hashing and constant-time verification."""

import asyncio
import hashlib
import hmac
import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Final

import bcrypt
import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from fastapi import HTTPException, status

from app.core.config import get_settings

logger = logging.getLogger(__name__)

# OWASP Recommended Argon2id Parameters
ARGON2_TIME_COST: Final[int] = 3
ARGON2_MEMORY_COST: Final[int] = 65536  # 64 MB
ARGON2_PARALLELISM: Final[int] = 4
ARGON2_HASH_LEN: Final[int] = 32
ARGON2_SALT_LEN: Final[int] = 16

# Legacy PBKDF2 parameters for backward compatibility verification
PBKDF2_ALGORITHM: Final[str] = "sha256"
PBKDF2_ITERATIONS: Final[int] = 100_000

_argon2_hasher = PasswordHasher(
    time_cost=ARGON2_TIME_COST,
    memory_cost=ARGON2_MEMORY_COST,
    parallelism=ARGON2_PARALLELISM,
    hash_len=ARGON2_HASH_LEN,
    salt_len=ARGON2_SALT_LEN,
)


def hash_password(password: str) -> str:
    """CPU/Memory-bound password hashing using OWASP-recommended Argon2id."""
    return _argon2_hasher.hash(password)


def _sync_verify_pbkdf2(plain_password: str, hashed_password: str) -> bool:
    """Verify legacy PBKDF2 password hash using constant-time comparison."""
    try:
        header, salt_hex, hash_hex = hashed_password.split("$")
        algo_info = header.split(":")
        algorithm = algo_info[1]
        iterations = int(algo_info[2])
        salt = bytes.fromhex(salt_hex)
        expected_hash = bytes.fromhex(hash_hex)
        derived = hashlib.pbkdf2_hmac(
            algorithm,
            plain_password.encode("utf-8"),
            salt,
            iterations,
        )
        return hmac.compare_digest(derived, expected_hash)
    except Exception:
        return False


def _sync_verify_bcrypt(plain_password: str, hashed_password: str) -> bool:
    """Verify legacy Bcrypt password hash using native constant-time comparison."""
    try:
        return bcrypt.checkpw(
            plain_password.encode("utf-8"),
            hashed_password.encode("utf-8"),
        )
    except Exception:
        return False


def verify_password_sync(plain_password: str, hashed_password: str) -> bool:
    """Constant-time verification supporting Argon2id, PBKDF2, and Bcrypt formats."""
    if not plain_password or not hashed_password:
        return False

    if hashed_password.startswith("$argon2"):
        try:
            _argon2_hasher.verify(hashed_password, plain_password)
            return True
        except (VerifyMismatchError, VerificationError, InvalidHashError):
            return False

    if hashed_password.startswith("pbkdf2:"):
        return _sync_verify_pbkdf2(plain_password, hashed_password)

    if hashed_password.startswith(("$2a$", "$2b$", "$2y$")):
        return _sync_verify_bcrypt(plain_password, hashed_password)

    return False


def needs_rehash(hashed_password: str) -> bool:
    """Determine whether stored hash should be upgraded to current Argon2id parameters."""
    if not hashed_password or not hashed_password.startswith("$argon2"):
        return True
    try:
        return _argon2_hasher.check_needs_rehash(hashed_password)
    except (VerificationError, InvalidHashError):
        return True


async def hash_password_async(password: str) -> str:
    """Asynchronously derive an Argon2id hash offloaded to a worker thread."""
    return await asyncio.to_thread(hash_password, password)


async def verify_password_async(plain_password: str, hashed_password: str) -> bool:
    """Asynchronously verify a password offloading computation to a worker thread."""
    return await asyncio.to_thread(verify_password_sync, plain_password, hashed_password)


async def needs_rehash_async(hashed_password: str) -> bool:
    """Asynchronously evaluate whether a hash requires parameter or algorithm upgrade."""
    return await asyncio.to_thread(needs_rehash, hashed_password)


# Backward-compatibility aliases for existing routers and services
get_password_hash = hash_password_async
verify_password = verify_password_async


def create_access_token(
    user_id: int,
    role: str,
    permissions: int | None = None,
    expires_delta: timedelta | None = None,
    algorithm: str | None = None,
    secret_or_private_key: str | None = None,
) -> str:
    """Create short-lived cryptographically signed stateless JWT access token.

    Defaults to 15-minute expiration and HS256/RS256 signing.
    """
    settings = get_settings()
    algo = algorithm or settings.jwt_algorithm
    if secret_or_private_key is not None:
        key = secret_or_private_key
    elif algo.startswith("RS"):
        if not settings.jwt_private_key:
            raise ValueError("RS256 signing requires jwt_private_key to be configured.")
        key = settings.jwt_private_key
    else:
        key = settings.jwt_secret_key

    now = datetime.now(UTC)
    expire_duration = (
        expires_delta if expires_delta is not None else timedelta(minutes=settings.jwt_access_token_expire_minutes)
    )
    expire = now + expire_duration

    if permissions is None:
        if role == "admin":
            perms = 63
        elif role == "moderator":
            perms = 7
        elif role == "guest":
            perms = 1
        else:
            perms = 3
    else:
        perms = permissions

    payload: dict[str, Any] = {
        "sub": str(user_id),
        "role": role,
        "permissions": perms,
        "token_type": "access",
        "jti": uuid.uuid4().hex,
        "iat": int(now.timestamp()),
        "exp": int(expire.timestamp()),
    }
    return jwt.encode(payload, key, algorithm=algo)


def create_refresh_token(
    user_id: int,
    family_id: str,
    expires_delta: timedelta | None = None,
    algorithm: str | None = None,
    secret_or_private_key: str | None = None,
) -> tuple[str, str]:
    """Create long-lived cryptographically signed JWT refresh token within a rotation family.

    Defaults to 7-day expiration and returns tuple of (encoded_token, jti).
    """
    settings = get_settings()
    algo = algorithm or settings.jwt_algorithm
    if secret_or_private_key is not None:
        key = secret_or_private_key
    elif algo.startswith("RS"):
        if not settings.jwt_private_key:
            raise ValueError("RS256 signing requires jwt_private_key to be configured.")
        key = settings.jwt_private_key
    else:
        key = settings.jwt_secret_key

    now = datetime.now(UTC)
    expire_duration = (
        expires_delta if expires_delta is not None else timedelta(days=settings.jwt_refresh_token_expire_days)
    )
    expire = now + expire_duration
    token_jti = uuid.uuid4().hex

    payload: dict[str, Any] = {
        "sub": str(user_id),
        "family_id": family_id,
        "token_type": "refresh",
        "jti": token_jti,
        "iat": int(now.timestamp()),
        "exp": int(expire.timestamp()),
    }
    encoded_token = jwt.encode(payload, key, algorithm=algo)
    return encoded_token, token_jti


def decode_jwt_token(
    token: str,
    expected_type: str | None = None,
    algorithm: str | None = None,
    secret_or_public_key: str | None = None,
) -> dict[str, Any]:
    """Cryptographically verify signature and expiration of JWT token in O(1) time.

    Verifies signature using HS256 symmetric secret or RS256 public key, validates
    standard exp/sub claims, and asserts expected token_type.
    """
    settings = get_settings()
    algo = algorithm or settings.jwt_algorithm
    key: str
    if secret_or_public_key is not None:
        key = secret_or_public_key
    elif algo.startswith("RS"):
        rs_key = settings.jwt_public_key or settings.jwt_private_key
        if not rs_key:
            raise ValueError("RS256 verification requires jwt_public_key or jwt_private_key.")
        key = rs_key
    else:
        key = settings.jwt_secret_key

    try:
        payload: dict[str, Any] = jwt.decode(
            token,
            key=key,
            algorithms=[algo],
            options={"require": ["exp", "sub", "token_type"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has expired",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
    except jwt.InvalidTokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication token",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    if expected_type is not None:
        actual_type = payload.get("token_type")
        if actual_type != expected_type:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=f"Invalid token type: expected '{expected_type}', got '{actual_type}'",
                headers={"WWW-Authenticate": "Bearer"},
            )

    return payload
