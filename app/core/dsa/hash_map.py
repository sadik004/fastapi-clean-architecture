"""CPython Compact Hash Map Implementation with Perturbation Open Addressing.

Demonstrates:
1. CPython 3.6+ Compact Dict Architecture:
   - Sparse `_indices` array storing small integer offsets (-1 for empty, -2 for dummy tombstone).
   - Dense `_entries` array storing (hash, key, value) sequentially in insertion order.
2. Open Addressing with CPython Perturbation Recurrence:
   - Recurrence relation: i = ((5 * i) + 1 + perturb) & mask, perturb >>= 5
   - Power-of-2 capacity guarantee: full period traversal of all slots.
   - High-order hash dispersion: eliminates primary clustering under heavy collisions.
3. 2/3 (0.66) Load Factor Resizing:
   - Dynamic doubling of capacity when entries reach 2/3 of capacity.
   - Automatic tombstone purging and dense entry compaction during resize.
"""

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any, Generic, TypeVar

K = TypeVar("K")
V = TypeVar("V")

# Sentinel slot values in sparse indices array
EMPTY: int = -1
DUMMY: int = -2
INITIAL_CAPACITY: int = 8


@dataclass
class Entry(Generic[K, V]):
    """Dense entry record storing key, value, cached hash, and activity flag."""

    hash: int
    key: K
    value: V
    is_active: bool = True


class CompactHashMap(Generic[K, V]):
    """Production-grade compact hash map mirroring CPython dictionary internals.

    Time Complexity:
    - Lookup: Amortized O(1), worst-case O(N) under pathological collisions.
    - Insert: Amortized O(1), worst-case O(N) during capacity doubling resize.
    - Delete: Amortized O(1).
    - Iteration: O(N) strictly contiguous dense array traversal in insertion order.

    Space Complexity:
    - O(N) compact representation: sparse integer table + dense sequential entries.
    """

    def __init__(self, initial_capacity: int = INITIAL_CAPACITY) -> None:
        # Enforce power-of-two capacity
        capacity = INITIAL_CAPACITY
        while capacity < initial_capacity:
            capacity <<= 1
        self._capacity: int = capacity

        # Sparse array storing indices into self._entries
        self._indices: list[int] = [EMPTY] * self._capacity

        # Dense array storing active and deleted entries in chronological insertion order
        self._entries: list[Entry[K, V]] = []

        # Number of active, non-deleted entries
        self._size: int = 0

        # Telemetry and collision diagnostics
        self._collision_count: int = 0
        self._max_probe_depth: int = 0

    @property
    def capacity(self) -> int:
        """Current allocated capacity of the sparse indices array."""
        return self._capacity

    @property
    def size(self) -> int:
        """Current number of active keys in the hash map."""
        return self._size

    @property
    def load_factor(self) -> float:
        """Current ratio of entries to capacity."""
        return len(self._entries) / self._capacity

    def _lookup(self, key: K, hash_val: int) -> tuple[int, int, int | None]:
        """Probe for a key using CPython's open-addressing perturbation algorithm.

        Returns:
            (slot_index, entry_index, first_dummy_slot)
            - slot_index: The slot in _indices where key was found or EMPTY slot.
            - entry_index: Offset in _entries if key was found, else EMPTY.
            - first_dummy_slot: First tombstone slot encountered during probe, if any.
        """
        mask = self._capacity - 1
        i = hash_val & mask
        perturb = hash_val
        first_dummy: int | None = None
        probe_depth = 0

        while True:
            idx = self._indices[i]
            if idx == EMPTY:
                # Key is not in table; return target insertion slot
                target_slot = first_dummy if first_dummy is not None else i
                if probe_depth > self._max_probe_depth:
                    self._max_probe_depth = probe_depth
                return (target_slot, EMPTY, first_dummy)

            if idx == DUMMY:
                # Tombstone found; save first dummy slot for reuse on insertion
                if first_dummy is None:
                    first_dummy = i
            else:
                entry = self._entries[idx]
                if entry.is_active and entry.hash == hash_val and entry.key == key:
                    # Key found!
                    if probe_depth > self._max_probe_depth:
                        self._max_probe_depth = probe_depth
                    return (i, idx, first_dummy)

            # Slot was occupied by a different key or dummy; probe collision
            self._collision_count += 1
            probe_depth += 1

            # CPython recurrence formula: 5*i + 1 + perturb (mod 2^k)
            i = ((5 * i) + 1 + perturb) & mask
            perturb >>= 5

    def _resize(self, new_capacity: int) -> None:
        """Double table capacity, compact dense entries, and rebuild sparse indices."""
        old_entries = self._entries
        self._entries = []
        self._indices = [EMPTY] * new_capacity
        self._capacity = new_capacity
        mask = new_capacity - 1

        for entry in old_entries:
            if not entry.is_active:
                continue

            new_entry_idx = len(self._entries)
            self._entries.append(entry)

            # Re-index active entry into new sparse array
            perturb = entry.hash
            i = entry.hash & mask
            probe_depth = 0
            while self._indices[i] != EMPTY:
                self._collision_count += 1
                probe_depth += 1
                i = ((5 * i) + 1 + perturb) & mask
                perturb >>= 5

            if probe_depth > self._max_probe_depth:
                self._max_probe_depth = probe_depth

            self._indices[i] = new_entry_idx

    def __setitem__(self, key: K, value: V) -> None:
        """Insert or update a key-value pair."""
        hash_val = hash(key)
        slot, entry_idx, first_dummy = self._lookup(key, hash_val)

        if entry_idx != EMPTY:
            # Key exists: update in-place without altering insertion order
            self._entries[entry_idx].value = value
            return

        # Check 2/3 load factor bound
        if (len(self._entries) + 1) * 3 >= self._capacity * 2:
            self._resize(self._capacity * 2)
            slot, _, _ = self._lookup(key, hash_val)

        new_entry_idx = len(self._entries)
        self._entries.append(Entry(hash=hash_val, key=key, value=value, is_active=True))
        self._indices[slot] = new_entry_idx
        self._size += 1

    def __getitem__(self, key: K) -> V:
        """Retrieve value for key or raise KeyError."""
        hash_val = hash(key)
        _, entry_idx, _ = self._lookup(key, hash_val)
        if entry_idx == EMPTY:
            raise KeyError(key)
        return self._entries[entry_idx].value

    def __contains__(self, key: object) -> bool:
        """Check if key exists in hash map."""
        try:
            hash_val = hash(key)
        except TypeError:
            return False
        _, entry_idx, _ = self._lookup(key, hash_val)  # type: ignore[arg-type]
        return entry_idx != EMPTY

    def __len__(self) -> int:
        """Return number of active elements."""
        return self._size

    def __iter__(self) -> Iterator[K]:
        """Iterate over active keys in strict chronological insertion order."""
        for entry in self._entries:
            if entry.is_active:
                yield entry.key

    def get(self, key: K, default: V | None = None) -> V | None:
        """Retrieve value for key or return default fallback."""
        try:
            return self[key]
        except KeyError:
            return default

    def delete(self, key: K) -> bool:
        """Remove key from hash map, placing a dummy tombstone in the sparse table."""
        hash_val = hash(key)
        slot, entry_idx, _ = self._lookup(key, hash_val)
        if entry_idx == EMPTY:
            return False

        # Mark entry inactive
        self._entries[entry_idx].is_active = False
        # Write dummy tombstone into sparse indices to preserve probe chains
        self._indices[slot] = DUMMY
        self._size -= 1
        return True

    def __delitem__(self, key: K) -> None:
        """Delete key or raise KeyError."""
        if not self.delete(key):
            raise KeyError(key)

    def keys(self) -> list[K]:
        """Return list of active keys in insertion order."""
        return [e.key for e in self._entries if e.is_active]

    def values(self) -> list[V]:
        """Return list of active values in insertion order."""
        return [e.value for e in self._entries if e.is_active]

    def items(self) -> list[tuple[K, V]]:
        """Return list of active (key, value) pairs in insertion order."""
        return [(e.key, e.value) for e in self._entries if e.is_active]

    def clear(self) -> None:
        """Reset hash map to initial empty state."""
        self._capacity = INITIAL_CAPACITY
        self._indices = [EMPTY] * self._capacity
        self._entries.clear()
        self._size = 0
        self._collision_count = 0
        self._max_probe_depth = 0

    def get_stats(self) -> dict[str, Any]:
        """Return internal hash table telemetry and collision metrics."""
        tombstone_count = sum(1 for idx in self._indices if idx == DUMMY)
        return {
            "size": self._size,
            "capacity": self._capacity,
            "load_factor": round(self.load_factor, 4),
            "entries_count": len(self._entries),
            "tombstone_count": tombstone_count,
            "collision_count": self._collision_count,
            "max_probe_depth": self._max_probe_depth,
        }


def diagnose_hash_health(hash_map: CompactHashMap[Any, Any]) -> dict[str, Any]:
    """Analyze collision patterns and evaluate health status of a hash map instance."""
    stats = hash_map.get_stats()
    size = stats["size"]
    collisions = stats["collision_count"]
    max_depth = stats["max_probe_depth"]

    collision_ratio = collisions / max(size, 1)
    is_pathological = max_depth > 20 or collision_ratio > 3.0

    status = "healthy"
    if is_pathological:
        status = "pathological_collision_attack_detected"
    elif collision_ratio > 1.0 or max_depth > 5:
        status = "moderate_collisions"

    return {
        "status": status,
        "collision_ratio": round(collision_ratio, 2),
        "max_probe_depth": max_depth,
        "is_pathological": is_pathological,
        "stats": stats,
    }


__all__ = [
    "CompactHashMap",
    "Entry",
    "diagnose_hash_health",
]
