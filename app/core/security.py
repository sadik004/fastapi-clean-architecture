"""Security utilities for non-blocking CPU-bound password hashing and constant-time verification."""

import asyncio
import hashlib
import hmac
import secrets

PBKDF2_ALGORITHM: str = "sha256"
PBKDF2_ITERATIONS: int = 100_000
SALT_SIZE_BYTES: int = 16


def _sync_hash_password(password: str) -> str:
    """CPU-bound password hashing using PBKDF2-HMAC-SHA256 with 100,000 iterations."""
    salt = secrets.token_bytes(SALT_SIZE_BYTES)
    derived = hashlib.pbkdf2_hmac(
        PBKDF2_ALGORITHM,
        password.encode("utf-8"),
        salt,
        PBKDF2_ITERATIONS,
    )
    salt_hex = salt.hex()
    hash_hex = derived.hex()
    return f"pbkdf2:{PBKDF2_ALGORITHM}:{PBKDF2_ITERATIONS}${salt_hex}${hash_hex}"


def _sync_verify_password(plain_password: str, hashed_password: str) -> bool:
    """CPU-bound constant-time password verification against stored hash."""
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


async def get_password_hash(password: str) -> str:
    """Asynchronously derive a password hash offloading CPU computation to a worker thread."""
    return await asyncio.to_thread(_sync_hash_password, password)


async def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Asynchronously verify a password offloading CPU computation to a worker thread."""
    return await asyncio.to_thread(_sync_verify_password, plain_password, hashed_password)
