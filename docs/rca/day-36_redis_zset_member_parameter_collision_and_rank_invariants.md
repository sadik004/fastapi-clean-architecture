# RCA: Day 36 - Redis ZSET Keyword Signature Collision (`value` vs `member`), 1-Indexed Rank Invariants & Skip List Benchmark Latency

- **Date**: 2026-09-09
- **Role**: Junior Apprentice Backend Engineer
- **Lead Architect & Mentor**: User
- **Topic**: Redis Sorted Sets (ZSET) Primitives, `redis-py` Driver Keyword Signature Alignment, 0-Indexed to 1-Indexed Rank Mapping, and Percentile Boundary Guarantees.

---

## 1. Trigger & Incident Scenarios

During the test execution of Day 36's Real-Time Leaderboard Service (`tests/test_leaderboard_zset.py`):

### Incident 1: `redis-py` Driver Signature Collision (`TypeError: unexpected keyword argument 'member'`)
When implementing the `CacheService` sorted set wrapper methods:
```python
async def zrevrank(self, key: str, member: str) -> int | None:
    rank = await self._redis.zrevrank(name=key, member=member)
    return int(rank) if rank is not None else None

async def zscore(self, key: str, member: str) -> float | None:
    score = await self._redis.zscore(name=key, member=member)
    return float(score) if score is not None else None
```
all 5 test suites crashed with:
```text
TypeError: SortedSetCommands.zrevrank() got an unexpected keyword argument 'member'
TypeError: SortedSetCommands.zscore() got an unexpected keyword argument 'member'
```

### Incident 2: 0-Indexed Redis vs 1-Indexed Human Presentation Off-by-One Hazard
In Redis internal architecture:
- `ZREVRANK` returns the 0-indexed position where `0` denotes the highest score (first place).
- If passed directly to consumers or API responses, the leader of the tournament is presented as `rank: 0`.
- Furthermore, in pagination slicing (`offset`, `limit`):
  - In `ZREVRANGE key start stop [WITHSCORES]`, `start` and `stop` are **inclusive** 0-based indexes.
  - Slicing 10 elements with `offset = 0` requires `start = 0, stop = 9`. Passing `stop = offset + limit = 10` would inadvertently return 11 elements, breaking pagination invariants.

### Incident 3: Percentile Boundary Asymmetry on Single-Player Competitions
When calculating player percentile:
$$\text{percentile} = \frac{\text{total} - \text{rank}}{\text{total}} \times 100$$
for a leaderboard with exactly $N=1$ competitor:
- $\text{rank} = 1, \text{total} = 1 \implies \frac{1 - 1}{1} \times 100 = 0.0\%$.
- Presenting a solo tournament winner as being in the "0th percentile" violates domain business rules.

---

## 2. Root Cause Analysis

### A. `redis-py` Method Signature Discrepancy
1. While the official Redis specification and CLI documentation refer to sorted set entries as `(score, member)`, the Python driver `redis-py` (and `fakeredis`) binds the member argument under the parameter name `value`:
   ```python
   # redis.asyncio signature:
   zrevrank(name: Union[bytes, str, memoryview], value: Union[bytes, memoryview, str, int, float], ...)
   zscore(name: Union[bytes, str, memoryview], value: Union[bytes, memoryview, str, int, float])
   zincrby(name: Union[bytes, str, memoryview], amount: float, value: Union[bytes, memoryview, str, int, float])
   ```
2. When calling `self._redis.zrevrank(name=key, member=member)` with explicit keyword arguments, Python's runtime argument parser rejects `'member'` with `TypeError`.
3. **Resolution**:
   Update `CacheService` to pass `value=member`:
   ```python
   async def zrevrank(self, key: str, member: str) -> int | None:
       rank = await self._redis.zrevrank(name=key, value=member)
       return int(rank) if rank is not None else None

   async def zscore(self, key: str, member: str) -> float | None:
       score = await self._redis.zscore(name=key, value=member)
       return float(score) if score is not None else None
   ```

### B. Translation of 0-Indexed Redis to 1-Indexed Clean Domain Rank
1. Clean Architecture mandates that domain models and public API contracts reflect business reality, not internal storage indexing quirks.
2. In `LeaderboardService.record_score`:
   ```python
   zero_rank = await self._cache.zrevrank(key, member=player_id)
   rank = (zero_rank + 1) if zero_rank is not None else 1
   ```
3. In `LeaderboardService.get_top_players`:
   The slice is fetched with `start = offset`, `stop = offset + limit - 1`.
   For item $i$ in the enumerated list, its rank is computed as:
   $$\text{rank} = \text{offset} + i + 1$$
   This prevents any secondary Redis roundtrips while ensuring strictly accurate 1-indexed ranks.

### C. Guarding the Single-Player Percentile Invariant
1. In competitive gaming and ranking systems, a single player holding 1st place in a pool of 1 is by definition at the top standing ($100.0\%$).
2. **Resolution**:
   In `LeaderboardService.get_player_standing`:
   ```python
   if total_players > 1:
       percentile = round(((total_players - rank) / total_players) * 100.0, 2)
   else:
       percentile = 100.0
   ```
   This guarantees that $N=1$ yields $100.0\%$, while for $N=5$, rank 1 yields $80.0\%$, and rank 5 yields $0.0\%$.

---

## 3. Mathematical & Algorithmic Complexities

| Operation | Implementation Primitive | Complexity | Theoretical Mechanism |
| :--- | :--- | :--- | :--- |
| **Score Update** | `ZINCRBY key delta member` | $\mathcal{O}(\log N)$ | Traverses Skip List levels using forward pointers; updates node and pointer ranks. |
| **Rank Lookup** | `ZREVRANK key member` | $\mathcal{O}(\log N)$ | Sums span distances across Skip List forward levels during traversal. |
| **Direct Score** | `ZSCORE key member` | $\mathcal{O}(1)$ | Direct dictionary lookup in Redis internal hash table mapping member $\to$ score. |
| **Top-M Range** | `ZREVRANGE key start stop` | $\mathcal{O}(\log N + M)$ | Traverses $\mathcal{O}(\log N)$ to find `start` rank node, then steps $M$ nodes via level-1 pointers. |
| **Total Count** | `ZCARD key` | $\mathcal{O}(1)$ | Reads cardinality counter stored directly in Redis sorted set object metadata. |

---

## 4. Prevention Rules & Codified Standards

1. **Rule 1 (`redis-py` Keyword Parameter Verification)**:
   Never assume Redis CLI argument names (`member`) match Python driver parameter signatures (`value`). Always inspect `inspect.signature(redis.zrevrank)` when wrapping client SDK calls.

2. **Rule 2 (1-Indexed Presentation Invariant)**:
   Never expose raw 0-indexed Redis ranks (`zrank`, `zrevrank`) in public REST DTOs or domain models. Always translate to 1-indexed integers (`rank = zrevrank + 1`).

3. **Rule 3 (Inclusive Range Slicing Guard)**:
   In Redis `ZREVRANGE` and `ZRANGE`, the `stop` parameter is inclusive. To retrieve $M$ elements starting at `offset`, always calculate $\text{stop} = \text{offset} + M - 1$.

4. **Rule 4 (Bounded Pagination on Sorted Sets)**:
   Never fetch unbounded sorted sets with `stop = -1` into application RAM. Always enforce strict pagination bounds (`limit: ge=1, le=100`, `offset: ge=0`).
