# Day 21: Python Dictionary Internals (Hash Table Collisions, Open Addressing & Compact Memory Layout)

**Date**: 2026-09-08  
**Role**: Junior Apprentice Backend Engineer  
**Lead Architect & Mentor**: User

---

## 1. Concepts Covered Today
- **CPython 3.6+ Compact Dictionary Architecture**:
  - Uncovered the memory inefficiency of historical hash tables that held large, sparse arrays of `(hash, key, value)` tuples directly in every slot (wasting 20–25% memory with empty slots).
  - Implemented CPython's two-tier compact storage model in `app/core/dsa/hash_map.py`:
    1. **Sparse `_indices` array**: Array of size $2^k$ containing small integer indices (`-1` for empty, `-2` for dummy tombstone, $\ge 0$ pointing into the dense array).
    2. **Dense `_entries` array**: Contiguous list storing `Entry(hash, key, value)` chronologically in exact insertion order.
  - Guaranteed deterministic insertion-order iteration over keys, values, and items.
- **Open Addressing with Perturbation Recurrence**:
  - Implemented CPython's exact pseudo-random linear congruential generator:
    $$i = ((5 \times i) + 1 + \text{perturb}) \pmod{2^k}, \quad \text{where } \text{perturb} \gets \text{perturb} \gg 5$$
  - Proved mathematically and empirically why this avoids primary clustering:
    - Because capacity is always a power of 2 ($2^k$), the recurrence $5i + 1 \pmod{2^k}$ produces a full-period permutation visiting **every single slot in the table**.
    - The 5-bit right shift factor incorporates higher-order hash bits on successive collision probes, rapidly scattering colliding keys across disjoint probe paths.
- **Tombstone Preservation on Deletion**:
  - Implemented `DUMMY = -2` tombstone markers in `_indices` upon element deletion.
  - Proved that tombstones prevent probe chains from terminating prematurely for subsequent colliding keys.
  - Compacted dense entries and reclaimed tombstone slots cleanly during dynamic resizing.
- **Strict 2/3 Load Factor Resizing**:
  - Bounded table load factor to $\le 2/3$ (`len(_entries) * 3 >= capacity * 2`).
  - Doubled capacity upon reaching threshold, resetting sparse indices and re-indexing all active entries in $\mathcal{O}(N)$ amortized time.
- **Hash DoS Attack Resilience & Health Telemetry**:
  - Built diagnostic health monitor `diagnose_hash_health` reporting collision ratios and max probe depth.
  - Simulated a 50-key hash collision attack (identical hash `42`); verified that perturbation probing safely resolves all 50 keys without data corruption or infinite loops.

---

## 2. DSA Time & Space Complexity Enforced
- **Lookup, Insertion, Deletion**:
  - Time Complexity: Strictly $\mathcal{O}(1)$ amortized.
  - Resizing: $\mathcal{O}(N)$ worst-case during dynamic doubling, bounded by $\mathcal{O}(1)$ amortized over arbitrary insertions.
- **Memory Layout & Space Complexity**:
  - Space Complexity: Strictly compact $\mathcal{O}(N)$ memory footprint.
  - Dense entries are contiguous in memory, maximizing CPU L1/L2 cache locality during iteration.
- **Full Period Traversal**:
  - $5i + 1 \pmod{2^k}$ guarantees that every slot is visited exactly once before repeating, eliminating infinite search loops when searching for empty slots.

---

## 3. Summary of Test Results & Quality Gates
- **Pytest Suite**: **293 passed** in 17.36s (`100%` pass rate across 36 test modules).
  - `tests/test_dict_internals_and_dsa.py`: 6 new comprehensive tests passing:
    1. `test_compact_hash_map_crud_and_correctness`: Validates 1,000 diverse insertions, lookups, updates, deletions, and KeyError exceptions.
    2. `test_forced_hash_collision_attack_resilience`: Injects 50 distinct keys with identical hash `42`, proving perturbation resolution without infinite loops.
    3. `test_load_factor_and_dynamic_resizing`: Validates capacity doubling at the 2/3 load factor threshold (8 -> 16 -> 32) with zero data loss.
    4. `test_tombstone_preserves_probe_chains_on_deletion`: Proves that deleting an intermediate colliding key does not break probe chains for subsequent keys.
    5. `test_compact_memory_layout_and_insertion_ordering`: Validates that `.keys()`, `.values()`, `.items()`, and `iter()` preserve insertion sequence.
    6. `test_hash_health_diagnostics`: Validates telemetry reporting and anomaly detection for healthy vs pathological collision rates.
- **Strict Type Checking (`mypy --strict app tests alembic`)**:
  - `Success: no issues found in 56 source files`.
- **Linter & Formatting (`ruff check app tests alembic`)**:
  - `All checks passed!`.
