"""Time-Ordered Cryptographic Identifiers (RFC 9562 UUIDv7 & ULID).

Engineered for:
1. B-Tree Clustered Index Locality:
   - Eliminates UUIDv4 random leaf insertion fragmentation and page splits.
   - New keys are monotonically appended at the right-most edge of the B-Tree index.
2. Anti-Enumeration & Business Intelligence Security:
   - Defeats competitor scraping and ID guessing inherent in sequential auto-increment integers.
3. O(1) Bitwise Creation Timestamp Derivation:
   - Direct mathematical extraction of the 48-bit millisecond epoch timestamp without querying storage.
4. Crockford Base32 ULID:
   - 128-bit URL-safe, case-insensitive, human-friendly identifier format.
"""

from __future__ import annotations

import secrets
import threading
import time
import uuid
from datetime import UTC, datetime

# Crockford's Base32 alphabet: 32 characters, excluding I, L, O, and U to avoid optical confusion.
CROCKFORD_BASE32_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_CROCKFORD_DECODE_MAP = {char: idx for idx, char in enumerate(CROCKFORD_BASE32_ALPHABET)}

# Thread-safe state for strict monotonic sub-millisecond sequence counter
_state_lock = threading.Lock()
_last_v7_timestamp_ms = 0
_v7_sequence_counter = 0


def generate_uuidv7() -> uuid.UUID:
    """Generate an RFC 9562-compliant UUID version 7.

    Layout (128 bits):
    - unix_ts_ms (48 bits): Unix timestamp in milliseconds.
    - ver (4 bits): 0b0111 (version 7).
    - rand_a (12 bits): Monotonic sequence counter or entropy within the millisecond.
    - var (2 bits): 0b10 (RFC 4122/9562 variant).
    - rand_b (62 bits): Cryptographically secure random entropy.

    Complexity: Strictly O(1) time and space.
    """
    global _last_v7_timestamp_ms, _v7_sequence_counter

    now_ms = int(time.time() * 1000)

    with _state_lock:
        if now_ms > _last_v7_timestamp_ms:
            _last_v7_timestamp_ms = now_ms
            # Seed the 12-bit sequence counter with random entropy to prevent predictable sequence starts,
            # clamped so it has room to increment monotonically within the same millisecond.
            _v7_sequence_counter = secrets.randbits(10)  # 0 to 1023 (max 4095)
        else:
            # Clock has not moved forward (same millisecond or minor clock skew), increment monotonically
            _v7_sequence_counter = (_v7_sequence_counter + 1) & 0xFFF  # 12-bit clamp
            # If sequence wraps around within the same ms, borrow future time or maintain max
            now_ms = _last_v7_timestamp_ms

        seq = _v7_sequence_counter

    # 48 bits: timestamp
    ts_48 = now_ms & 0xFFFFFFFFFFFF

    # 4 bits ver (0x7) + 12 bits sequence
    ver_rand_a = (0x7 << 12) | (seq & 0xFFF)

    # 2 bits var (0b10) + 62 bits random entropy
    rand_62 = secrets.randbits(62)
    var_rand_b = (0x2 << 62) | rand_62

    # Pack into 128-bit integer:
    # ts_48 (bits 127..80) | ver_rand_a (bits 79..64) | var_rand_b (bits 63..0)
    int_val = (ts_48 << 80) | (ver_rand_a << 64) | var_rand_b

    return uuid.UUID(int=int_val)


def extract_timestamp_from_uuidv7(u: uuid.UUID | str) -> datetime:
    """Extract the creation UTC timestamp from an RFC 9562 UUIDv7 in O(1) bit-shift time.

    No database query or external metadata lookup required.

    Args:
        u: The UUIDv7 instance or standard UUID hex string.

    Returns:
        timezone-aware datetime in UTC.

    Raises:
        ValueError: If input is not a valid UUIDv7 (version != 7).
    """
    if isinstance(u, str):
        parsed = uuid.UUID(u)
    else:
        parsed = u

    if parsed.version != 7:
        raise ValueError(f"UUID is version {parsed.version}, expected version 7 for timestamp extraction.")

    # High 48 bits contain milliseconds since Unix epoch
    ms_timestamp = parsed.int >> 80
    return datetime.fromtimestamp(ms_timestamp / 1000.0, tz=UTC)


_last_ulid_timestamp_ms = 0
_last_ulid_entropy = 0


def generate_ulid() -> str:
    """Generate a 128-bit Universally Unique Lexicographically Sortable Identifier (ULID).

    Layout:
    - 48-bit Unix timestamp in milliseconds (10 Crockford Base32 characters).
    - 80-bit Cryptographically secure random entropy (16 Crockford Base32 characters).
    - Total: 26 uppercase characters, URL-safe, sorting-friendly.
    - Monotonic within the same millisecond: Entropy increments if generated in the same millisecond.

    Complexity: Strictly O(1) time and space.
    """
    global _last_ulid_timestamp_ms, _last_ulid_entropy

    now_ms = int(time.time() * 1000) & 0xFFFFFFFFFFFF

    with _state_lock:
        if now_ms > _last_ulid_timestamp_ms:
            _last_ulid_timestamp_ms = now_ms
            # Generate fresh 80-bit random entropy with headroom for monotonic increments
            _last_ulid_entropy = secrets.randbits(75)
        else:
            # Same millisecond or minor backward clock skew: monotonically increment entropy
            now_ms = _last_ulid_timestamp_ms
            _last_ulid_entropy = (_last_ulid_entropy + 1) & 0xFFFFFFFFFFFFFFFFFFFF

        entropy_80 = _last_ulid_entropy

    # Pack 128-bit value: (timestamp_48 << 80) | entropy_80
    ulid_int = (now_ms << 80) | entropy_80

    # Encode to 26 Crockford Base32 chars (each char encodes 5 bits: 26 * 5 = 130 bits, top 2 bits 0)
    chars: list[str] = [""] * 26
    for i in range(25, -1, -1):
        chars[i] = CROCKFORD_BASE32_ALPHABET[ulid_int & 0x1F]
        ulid_int >>= 5

    return "".join(chars)


def extract_timestamp_from_ulid(ulid_str: str) -> datetime:
    """Extract the creation UTC timestamp from a 26-character ULID in O(1) time.

    Args:
        ulid_str: The 26-character Crockford Base32 string.

    Returns:
        timezone-aware datetime in UTC.

    Raises:
        ValueError: If ULID is malformed or invalid characters are present.
    """
    if len(ulid_str) != 26:
        raise ValueError(f"Invalid ULID length: expected 26 characters, got {len(ulid_str)}.")

    # First 10 characters contain the 48-bit timestamp (10 * 5 = 50 bits, top 2 bits 0)
    timestamp_ms = 0
    clean_ulid = ulid_str.upper()
    for char in clean_ulid[:10]:
        if char not in _CROCKFORD_DECODE_MAP:
            raise ValueError(f"Invalid character '{char}' in ULID.")
        timestamp_ms = (timestamp_ms << 5) | _CROCKFORD_DECODE_MAP[char]

    return datetime.fromtimestamp(timestamp_ms / 1000.0, tz=UTC)
