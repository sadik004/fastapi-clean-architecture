"""Opaque URL-Safe Base64 Cursor Token Serialization & Deserialization Engine.

Guarantees:
1. Information Hiding: Database primary keys and raw timestamps are never exposed directly in query parameters.
2. Tamper-Proof Structure: Strict JSON decoding, element boundary checks, and ISO-8601 validation.
3. Fail-Fast Domain Errors: Any corrupted or malformed token immediately raises InvalidCursorException.
"""

from __future__ import annotations

import base64
import binascii
import json
from datetime import UTC, datetime
from typing import Any

from app.core.exceptions import InvalidCursorException


class CursorCodec:
    """Codec for bidirectional serialization between composite cursor tuples and URL-safe Base64 strings."""

    @staticmethod
    def encode_cursor(created_at: datetime, id: Any) -> str:
        """Serialize timestamp and tie-breaker ID into an opaque URL-safe Base64 token.

        Args:
            created_at: The timestamp anchor for keyset ordering.
            id: The unique primary key / tie-breaker identifier.

        Returns:
            URL-safe Base64 encoded cursor token string without trailing padding issues.
        """
        # Ensure UTC timezone awareness
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=UTC)
        else:
            created_at = created_at.astimezone(UTC)

        # Convert UUID to str if necessary
        serialized_id: Any = str(id) if hasattr(id, "hex") else id

        payload = [created_at.isoformat(), serialized_id]
        json_bytes = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        return base64.urlsafe_b64encode(json_bytes).decode("ascii").rstrip("=")

    @staticmethod
    def decode_cursor(cursor_str: str) -> tuple[datetime, Any]:
        """Decode and validate an opaque URL-safe Base64 cursor token into (created_at, id).

        Args:
            cursor_str: The opaque Base64 cursor token provided by client.

        Returns:
            Tuple of (timezone-aware UTC datetime, tie-breaker id).

        Raises:
            InvalidCursorException: If cursor is empty, corrupted, tampered, or invalid structure.
        """
        if not cursor_str or not isinstance(cursor_str, str):
            raise InvalidCursorException("Pagination cursor token cannot be empty or non-string.")

        clean_cursor = cursor_str.strip()
        if not clean_cursor:
            raise InvalidCursorException("Pagination cursor token cannot be whitespace.")

        # Re-add URL-safe Base64 padding if stripped
        padding_needed = (4 - len(clean_cursor) % 4) % 4
        padded_cursor = clean_cursor + ("=" * padding_needed)

        try:
            raw_bytes = base64.urlsafe_b64decode(padded_cursor.encode("ascii"))
            payload: Any = json.loads(raw_bytes.decode("utf-8"))
        except (binascii.Error, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            raise InvalidCursorException(f"Invalid, malformed, or tampered pagination cursor token: {exc}") from exc

        if not isinstance(payload, list | tuple) or len(payload) != 2:
            raise InvalidCursorException("Invalid cursor structure: expected composite tuple [created_at, id].")

        iso_timestamp, raw_id = payload

        if not isinstance(iso_timestamp, str) or raw_id is None:
            raise InvalidCursorException("Invalid cursor fields: timestamp must be string and id must not be null.")

        try:
            parsed_dt = datetime.fromisoformat(iso_timestamp)
            if parsed_dt.tzinfo is None:
                parsed_dt = parsed_dt.replace(tzinfo=UTC)
            else:
                parsed_dt = parsed_dt.astimezone(UTC)
        except (ValueError, TypeError) as exc:
            raise InvalidCursorException(f"Invalid ISO-8601 timestamp in cursor: {exc}") from exc

        return parsed_dt, raw_id
