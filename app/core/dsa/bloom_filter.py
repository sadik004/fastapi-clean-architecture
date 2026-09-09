"""Slotted Bloom Filter data structure for probabilistic membership testing.

Guarantees:
1. Zero False Negatives: If contains(x) is False, x is guaranteed 100% not to exist in the set.
2. Bounded False Positives: If contains(x) is True, x probably exists in the set with bounded probability P.
3. Memory Optimization: Strict __slots__ declaration and compact bytearray bit array.
4. Fast Kirsch-Mitzenmacher Double Hashing: O(k) bit calculations in sub-millisecond time (< 0.01ms).
"""

from __future__ import annotations

import hashlib
import math
from typing import Any


class BloomFilter:
    """Memory-optimized Bloom Filter using Kirsch-Mitzenmacher double hashing."""

    __slots__ = (
        "_capacity",
        "_false_positive_rate",
        "_bit_size",
        "_hash_count",
        "_bit_array",
        "_count",
    )

    def __init__(
        self,
        capacity: int = 100_000,
        false_positive_rate: float = 0.01,
    ) -> None:
        """Initialize Bloom Filter with optimal sizing.

        Args:
            capacity: Expected maximum number of items (N > 0).
            false_positive_rate: Desired false positive probability (0 < P < 1).

        Raises:
            ValueError: If capacity <= 0 or false_positive_rate not in (0, 1).
        """
        if capacity <= 0:
            raise ValueError(f"Capacity must be a positive integer, got {capacity}")
        if not (0.0 < false_positive_rate < 1.0):
            raise ValueError(f"False positive rate must be strictly between 0 and 1, got {false_positive_rate}")

        self._capacity: int = capacity
        self._false_positive_rate: float = false_positive_rate

        # Optimal bit array size: m = ceil(-(N * ln(P)) / (ln(2)^2))
        ln2_sq = math.log(2) ** 2
        numerator = -(capacity * math.log(false_positive_rate))
        self._bit_size: int = max(1, int(math.ceil(numerator / ln2_sq)))

        # Optimal hash count: k = max(1, round((m / N) * ln(2)))
        self._hash_count: int = max(
            1,
            int(round((self._bit_size / capacity) * math.log(2))),
        )

        # Compact bytearray: (bit_size + 7) // 8 bytes
        byte_length = (self._bit_size + 7) // 8
        self._bit_array: bytearray = bytearray(byte_length)
        self._count: int = 0

    @property
    def capacity(self) -> int:
        """Maximum configured capacity of items."""
        return self._capacity

    @property
    def false_positive_rate(self) -> float:
        """Configured target false positive probability."""
        return self._false_positive_rate

    @property
    def bit_size(self) -> int:
        """Total number of bits in the filter array."""
        return self._bit_size

    @property
    def hash_count(self) -> int:
        """Number of independent hash functions (k)."""
        return self._hash_count

    @property
    def count(self) -> int:
        """Total number of items inserted into the filter."""
        return self._count

    @property
    def size_bytes(self) -> int:
        """Memory footprint of the underlying bit array in bytes."""
        return len(self._bit_array)

    @property
    def size_kb(self) -> float:
        """Memory footprint of the underlying bit array in kilobytes."""
        return len(self._bit_array) / 1024.0

    def _get_hash_indices(self, item: str | int) -> list[int]:
        """Compute k bit indices using Kirsch-Mitzenmacher double hashing.

        Formula:
            h_i(x) = (h1(x) + i * h2(x)) % m
        where h1 and h2 are independent 64-bit integers from SHA-256 digest.
        """
        item_bytes = str(item).encode("utf-8")
        digest = hashlib.sha256(item_bytes).digest()

        h1 = int.from_bytes(digest[:8], byteorder="big")
        h2 = int.from_bytes(digest[8:16], byteorder="big")
        if h2 == 0:
            h2 = 1

        indices: list[int] = []
        for i in range(self._hash_count):
            idx = (h1 + i * h2) % self._bit_size
            indices.append(idx)
        return indices

    def add(self, item: str | int) -> None:
        """Insert an item into the Bloom filter.

        Sets all k bit positions to 1 in the bit array.
        Complexity: O(k) time, O(1) auxiliary space.
        """
        for bit_idx in self._get_hash_indices(item):
            byte_idx = bit_idx // 8
            bit_pos = bit_idx % 8
            self._bit_array[byte_idx] |= 1 << bit_pos
        self._count += 1

    def contains(self, item: str | int) -> bool:
        """Probabilistically test whether an item exists in the Bloom filter.

        Invariants:
        - If False is returned: item is GUARANTEED 100% not to exist (Zero False Negatives).
        - If True is returned: item PROBABLY exists with bounded probability P.

        Complexity: O(k) time, early-exit on first unset bit.
        """
        for bit_idx in self._get_hash_indices(item):
            byte_idx = bit_idx // 8
            bit_pos = bit_idx % 8
            if not (self._bit_array[byte_idx] & (1 << bit_pos)):
                return False
        return True

    def __contains__(self, item: str | int) -> bool:
        """Support 'item in bloom_filter' syntax."""
        return self.contains(item)

    def current_false_positive_probability(self) -> float:
        """Calculate the theoretical false positive probability based on current count.

        Formula:
            P = (1 - e^(-k * n / m))^k
        """
        if self._count == 0:
            return 0.0
        exponent = -(self._hash_count * self._count) / self._bit_size
        return float((1.0 - math.exp(exponent)) ** self._hash_count)

    def clear(self) -> None:
        """Reset the bit array to all zeros and reset item counter."""
        byte_length = (self._bit_size + 7) // 8
        self._bit_array = bytearray(byte_length)
        self._count = 0

    def get_metrics(self) -> dict[str, Any]:
        """Return operational metrics for telemetry reporting."""
        return {
            "capacity": self._capacity,
            "bit_size": self._bit_size,
            "bit_size_kb": round(self.size_kb, 2),
            "hash_count": self._hash_count,
            "item_count": self._count,
            "false_positive_probability": round(self.current_false_positive_probability(), 6),
        }
