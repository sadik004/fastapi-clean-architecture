"""Field-Level Encryption (FLE) engine and transparent SQLAlchemy TypeDecorator using Fernet."""

from functools import lru_cache
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import String
from sqlalchemy.types import TypeDecorator

from app.core.config import get_settings
from app.core.exceptions import EncryptionTamperingException


class FernetEngine:
    """Cryptographic engine providing authenticated symmetric encryption via Fernet (AES-128-CBC + HMAC-SHA256)."""

    def __init__(self, key: str | bytes | None = None) -> None:
        if key is None:
            raw_key = get_settings().field_encryption_key
        elif isinstance(key, str):
            raw_key = key
        else:
            raw_key = key.decode("utf-8")

        self._key: bytes = raw_key.encode("utf-8") if isinstance(raw_key, str) else raw_key
        self._cipher = Fernet(self._key)

    def encrypt(self, plaintext: str) -> str:
        """Encrypt plaintext string into authenticated Fernet ciphertext."""
        token_bytes = self._cipher.encrypt(plaintext.encode("utf-8"))
        return token_bytes.decode("utf-8")

    def decrypt(self, ciphertext: str) -> str:
        """Decrypt Fernet ciphertext and verify HMAC integrity.

        Raises:
            EncryptionTamperingException: If ciphertext is altered, truncated, corrupted,
                or HMAC authentication signature verification fails.
        """
        try:
            decrypted_bytes = self._cipher.decrypt(ciphertext.encode("utf-8"))
            return decrypted_bytes.decode("utf-8")
        except (InvalidToken, Exception) as exc:
            raise EncryptionTamperingException(
                f"Ciphertext verification failed or payload has been tampered with: {exc}"
            ) from exc


@lru_cache
def get_fernet_engine() -> FernetEngine:
    """Cached singleton provider for FernetEngine."""
    return FernetEngine()


class EncryptedString(TypeDecorator[str]):
    """Transparent SQLAlchemy TypeDecorator for Field-Level Encryption (FLE).

    Automatically encrypts plaintext strings to Fernet ciphertext before binding to SQL queries,
    and transparently decrypts Fernet ciphertext to plaintext strings upon result fetching.
    """

    impl = String
    cache_ok = True

    def __init__(self, length: int = 512, engine: FernetEngine | None = None, **kwargs: Any) -> None:
        super().__init__(length=length, **kwargs)
        self._engine = engine

    @property
    def engine(self) -> FernetEngine:
        """Resolve FernetEngine instance."""
        if self._engine is None:
            return get_fernet_engine()
        return self._engine

    def process_bind_param(self, value: str | None, dialect: Any) -> str | None:
        """Encrypt plaintext string to ciphertext prior to INSERT/UPDATE execution."""
        if value is None:
            return None
        return self.engine.encrypt(value)

    def process_result_value(self, value: str | None, dialect: Any) -> str | None:
        """Decrypt ciphertext to plaintext string upon SELECT execution."""
        if value is None:
            return None
        return self.engine.decrypt(value)
