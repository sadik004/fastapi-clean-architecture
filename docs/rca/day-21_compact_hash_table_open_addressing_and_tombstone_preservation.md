# RCA: Day 21 - Open Addressing Perturbation Probing, Tombstone Probe Preservation & Load Factor Bound

- **Date**: 2026-09-08
- **Role**: Junior Apprentice Backend Engineer
- **Lead Architect & Mentor**: User
- **Topic**: CPython Dictionary Internals, Hash Collisions & Tombstone Chain Integrity

---

## 1. Trigger
During Day 21 implementation of `CompactHashMap`:
1. Deleting intermediate keys caused subsequent colliding keys to become unreachable, raising unexpected `KeyError` on valid keys.
2. Naive linear probing (`(i + 1) % size`) caused primary clustering and probe degradation under hash collision attack simulations (50 keys with hash `42`).
3. Failing to resize before the table reached capacity caused infinite loops during empty slot resolution.

---

## 2. Faulty Code / Pattern

### Issue A: Deletion Severing the Collision Chain
```python
def delete(self, key: K) -> None:
    idx, entry_idx = self._lookup(key)
    if entry_idx < 0:
        raise KeyError(key)
    self._indices[idx] = -1  # Resetting slot to EMPTY (-1)!
    self._entries[entry_idx].is_active = False
```
When `_indices[idx]` is reset to `-1` (EMPTY), any subsequent key that collided with `key` and was placed further along the open-addressing probe chain is severed. The lookup algorithm encounters `-1` and concludes that the key does not exist!

### Issue B: Naive Linear Probing
```python
# Naive linear probing
i = (i + 1) & mask
```
Linear probing causes adjacent occupied slots to merge into massive contiguous clusters, degrading average lookup time from $\mathcal{O}(1)$ to $\mathcal{O}(N)$ under high load factors.

---

## 3. Root Cause
1. **Probe Chain Invariant Violation**: In open-addressed hash tables, an empty slot (`-1`) marks the termination of a search chain. Setting a deleted slot to empty breaks the invariant that all colliding keys can be located by traversing the probe sequence.
2. **Primary Clustering**: Naive linear probing has no variance based on high-order hash bits; every collision follows the exact same consecutive step size, compounding cluster sizes.
3. **Table Saturation**: As load factor approaches 1.0, probe sequences grow exponentially without bound.

---

## 4. Resolution

### Solution A: Tombstone Preservation (`DUMMY = -2`)
Preserved the probe chain on deletion using a dedicated dummy marker:
```python
DUMMY = -2

def delete(self, key: K) -> None:
    idx, entry_idx = self._lookup(key)
    if entry_idx < 0:
        raise KeyError(key)
    self._indices[idx] = DUMMY  # Tombstone preserves downstream probe chain
    self._entries[entry_idx].is_active = False
    self._count -= 1
```
During lookup, when encountering `DUMMY`, the algorithm records the first available tombstone index (for recycling on insertions) but continues probing until an empty slot (`-1`) or matching key is found.

### Solution B: CPython Perturbation Probing
Implemented CPython's linear congruential recurrence with a 5-bit right shift:
```python
perturb = h & 0xFFFFFFFF
while True:
    idx = i & mask
    # ... check slot ...
    i = (5 * i) + 1 + perturb
    perturb >>= 5
```
Because the table size is a power of 2 ($2^k$), the recurrence $5i + 1 \pmod{2^k}$ produces a full-period permutation visiting every slot, while `perturb >>= 5` incorporates high-order hash bits to scatter collisions across disjoint probe paths.

### Solution C: Strict 2/3 Load Factor Resizing
Enforced dynamic doubling when entries exceed 2/3 capacity:
```python
if len(self._entries) * 3 >= self._capacity * 2:
    self._resize(self._capacity * 2)
```
During resizing, tombstones are eliminated and active entries are compacted into a clean contiguous array.

---

## 5. Permanent Prevention Rule
1. **Tombstone Invariant**: Never replace deleted entries with empty markers in open-addressed hash tables. Always use tombstones (`DUMMY`).
2. **CPython Perturbation Formula**: Always utilize CPython's recurrence $i = ((5 \times i) + 1 + \text{perturb}) \pmod{2^k}$ for power-of-2 open-addressed tables.
3. **Strict 2/3 Load Factor Bound**: Never permit in-memory open-addressed tables to exceed 2/3 load factor without triggering dynamic resizing and tombstone compaction.
