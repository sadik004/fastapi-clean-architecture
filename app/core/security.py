"""Security utilities for memory-hard Argon2id password hashing and constant-time verification."""

import asyncio
import hashlib
import hmac
import logging
from typing import Final

import bcrypt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

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
