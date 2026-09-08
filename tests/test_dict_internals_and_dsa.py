"""Comprehensive test suite for Day 21: Python Dictionary Internals & Compact Hash Map.

Verifies:
1. CRUD correctness over 1,000 diverse items (integers, strings, floats, tuples).
2. Forced Hash Collision Resilience (DoS attack simulation): 50 distinct keys computing to identical hash.
3. Load Factor Bounding & Dynamic Resizing: 2/3 threshold triggers power-of-two capacity expansion.
4. Tombstone Probe Chain Preservation: Deletion writes dummy marker, keeping subsequent colliding keys retrievable.
5. Compact Memory Layout & Insertion Ordering: Keys, values, and items strictly follow insertion sequence.
6. Diagnostic Telemetry & Health Probe: get_stats() and diagnose_hash_health() accurately report metrics.
"""

from typing import Any

import pytest

from app.core.dsa.hash_map import (
    DUMMY,
    CompactHashMap,
    diagnose_hash_health,
)


class CollidingKey:
    """Deliberately pathological key class forcing identical 64-bit hash values for collision testing."""

    def __init__(self, key_id: int, label: str = "colliding") -> None:
        self.key_id = key_id
        self.label = label

    def __hash__(self) -> int:
        # Fixed constant hash forcing every single instance to map to the exact same initial slot
        return 42

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, CollidingKey):
            return False
        return self.key_id == other.key_id and self.label == other.label

    def __repr__(self) -> str:
        return f"CollidingKey({self.key_id}, '{self.label}')"


# ============================================================================
# 1. CRUD & Correctness Tests
# ============================================================================


def test_compact_hash_map_crud_and_correctness() -> None:
    """Verify set, get, update, delete, contains, and iteration for 1,000 diverse items."""
    hm: CompactHashMap[Any, Any] = CompactHashMap()
    assert len(hm) == 0
    assert hm.size == 0
    assert hm.capacity == 8

    # 1. Insert 1,000 diverse items
    expected_data: dict[Any, Any] = {}
    for i in range(1, 1001):
        if i % 4 == 0:
            key: Any = f"user_{i}"
        elif i % 4 == 1:
            key = i
        elif i % 4 == 2:
            key = (i, f"tag_{i}")
        else:
            key = float(i) * 1.5

        val = f"value_{i}"
        hm[key] = val
        expected_data[key] = val

    assert len(hm) == 1000
    assert hm.size == 1000

    # 2. Verify retrieval and containment
    for k, v in expected_data.items():
        assert k in hm
        assert hm[k] == v
        assert hm.get(k) == v

    # Missing key checks
    assert "non_existent_key_xyz" not in hm
    assert hm.get("non_existent_key_xyz") is None
    assert hm.get("non_existent_key_xyz", default="fallback") == "fallback"
    with pytest.raises(KeyError):
        _ = hm["non_existent_key_xyz"]

    # 3. In-place updates (size must remain constant)
    hm["user_4"] = "updated_val_4"
    assert hm["user_4"] == "updated_val_4"
    assert len(hm) == 1000

    # 4. Deletions
    assert hm.delete("user_4") is True
    assert "user_4" not in hm
    assert len(hm) == 999
    assert hm.delete("user_4") is False  # Second delete returns False

    with pytest.raises(KeyError):
        del hm["user_4"]


# ============================================================================
# 2. Forced Hash Collision Attack Resilience Test
# ============================================================================


def test_forced_hash_collision_attack_resilience() -> None:
    """Simulate a Hash DoS attack: insert 50 distinct keys with IDENTICAL hash values.

    Validates that CPython's perturbation formula i = ((5*i) + 1 + perturb) & mask
    resolves all 50 keys without infinite loops or data corruption.
    """
    hm: CompactHashMap[CollidingKey, str] = CompactHashMap()

    keys = [CollidingKey(key_id=i, label=f"exploit_{i}") for i in range(50)]

    # All keys have the exact same hash
    for k in keys:
        assert hash(k) == 42

    # Insert all 50 colliding keys
    for i, k in enumerate(keys):
        hm[k] = f"payload_{i}"

    assert len(hm) == 50

    # Verify that every single key can be looked up and returns its correct distinct payload
    for i, k in enumerate(keys):
        assert k in hm
        assert hm[k] == f"payload_{i}"

    # Verify collision telemetry recorded collisions
    stats = hm.get_stats()
    assert stats["collision_count"] > 0
    assert stats["max_probe_depth"] > 1

    # Verify updating a colliding key updates in-place
    hm[keys[10]] = "new_payload_10"
    assert hm[keys[10]] == "new_payload_10"
    assert len(hm) == 50


# ============================================================================
# 3. Load Factor Bounding & Dynamic Resizing Tests
# ============================================================================


def test_load_factor_and_dynamic_resizing() -> None:
    """Verify that reaching 2/3 load factor triggers doubling of capacity."""
    hm: CompactHashMap[str, int] = CompactHashMap(initial_capacity=8)
    assert hm.capacity == 8

    # Capacity = 8: 2/3 threshold is (len(_entries) + 1) * 3 >= 16 -> triggers at 5th or 6th element
    # 8 * 2/3 = 5.333, so 5 items are allowed; 6th item triggers resize to 16
    for i in range(5):
        hm[f"item_{i}"] = i
    assert hm.capacity == 8

    # 6th element triggers resize
    hm["item_5"] = 5
    assert hm.capacity == 16

    # Verify all previous items are still intact
    for i in range(6):
        assert hm[f"item_{i}"] == i

    # Fill up to 16 * 2/3 = 10.667 -> 11th item triggers resize to 32
    for i in range(6, 11):
        hm[f"item_{i}"] = i
    assert hm.capacity == 32

    # Verify all 11 items
    for i in range(11):
        assert hm[f"item_{i}"] == i

    assert hm.load_factor <= 2 / 3


# ============================================================================
# 4. Tombstone Probe Chain Preservation Tests
# ============================================================================


def test_tombstone_preserves_probe_chains_on_deletion() -> None:
    """Verify that deleting an entry writes a DUMMY tombstone, preserving probe chains for subsequent colliding keys."""
    hm: CompactHashMap[CollidingKey, str] = CompactHashMap(initial_capacity=8)

    # Create 3 keys with the exact same hash
    k1 = CollidingKey(1, "chain")
    k2 = CollidingKey(2, "chain")
    k3 = CollidingKey(3, "chain")

    hm[k1] = "first"
    hm[k2] = "second"
    hm[k3] = "third"

    assert len(hm) == 3

    # Delete the intermediate colliding key k2
    assert hm.delete(k2) is True
    assert k2 not in hm
    assert len(hm) == 2

    # CRITICAL INVARIANT: k3 MUST STILL BE RETRIEVABLE!
    # If deletion wrote EMPTY instead of DUMMY, the probe chain to k3 would be severed
    assert k3 in hm
    assert hm[k3] == "third"
    assert k1 in hm
    assert hm[k1] == "first"

    # Verify tombstone exists in internal indices
    assert DUMMY in hm._indices


# ============================================================================
# 5. Compact Memory Layout & Insertion Ordering Tests
# ============================================================================


def test_compact_memory_layout_and_insertion_ordering() -> None:
    """Verify that iterating keys, values, items strictly preserves chronological insertion order."""
    hm: CompactHashMap[str, int] = CompactHashMap()

    order = ["delta", "alpha", "charlie", "bravo", "echo"]
    for i, key in enumerate(order):
        hm[key] = i * 10

    # 1. Verify iteration matches insertion order
    assert list(hm) == order
    assert hm.keys() == order
    assert hm.values() == [0, 10, 20, 30, 40]
    assert hm.items() == [(k, i * 10) for i, k in enumerate(order)]

    # 2. Delete middle item ('charlie') and append new item ('foxtrot')
    del hm["charlie"]
    hm["foxtrot"] = 50

    expected_remaining = ["delta", "alpha", "bravo", "echo", "foxtrot"]
    assert list(hm) == expected_remaining
    assert hm.keys() == expected_remaining


# ============================================================================
# 6. Diagnostic Telemetry & Health Probe Tests
# ============================================================================


def test_hash_health_diagnostics() -> None:
    """Verify get_stats() telemetry and diagnose_hash_health() anomaly detection."""
    # Healthy map with normal keys
    healthy_map: CompactHashMap[str, int] = CompactHashMap()
    for i in range(100):
        healthy_map[f"clean_key_{i}"] = i

    healthy_diag = diagnose_hash_health(healthy_map)
    assert healthy_diag["status"] in ("healthy", "moderate_collisions")
    assert healthy_diag["is_pathological"] is False

    # Pathological map with 50 colliding keys
    attack_map: CompactHashMap[CollidingKey, int] = CompactHashMap()
    for i in range(50):
        attack_map[CollidingKey(i)] = i

    attack_diag = diagnose_hash_health(attack_map)
    assert attack_diag["is_pathological"] is True
    assert attack_diag["status"] == "pathological_collision_attack_detected"
    assert attack_diag["max_probe_depth"] > 10
