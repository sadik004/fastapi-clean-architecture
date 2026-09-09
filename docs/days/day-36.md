# Day 36: Real-Time Leaderboard Service Architecture using Redis Sorted Sets (ZSET)

**Date**: 2026-09-09  
**Role**: Junior Apprentice Backend Engineer  
**Lead Architect & Mentor**: User  
**Milestone**: Month 2 — Distributed Systems, Caching & Security  

---

## 1. Concepts Covered Today

- **The Relational Database Leaderboard Bottleneck**:
  - Traditional ranking systems query SQL tables using queries like `SELECT player_id, score FROM players ORDER BY score DESC LIMIT 10 OFFSET 0`.
  - In a multiplayer gaming, e-commerce trending, or real-time voting platform with millions of rows, updating a player's score requires an `UPDATE` on a heavily indexed column, creating row locks.
  - Sorting millions of rows on every write or read forces expensive disk-backed sort buffers, saturates database CPU cores, and spikes P99 latencies to hundreds or thousands of milliseconds.
- **Redis Sorted Sets (ZSET) Architecture**:
  - Redis ZSET is a specialized non-relational data structure uniquely designed for real-time ranking and scoring.
  - Internally, Redis couples two data structures simultaneously for the same key:
    1. **Skip List**: An ordered, probabilistic balanced tree alternative that maintains elements sorted strictly by floating-point score in $\mathcal{O}(\log N)$ insertion, update, and search time.
    2. **Hash Map**: Maps `member -> score` in $\mathcal{O}(1)$ time, allowing instant direct score queries.
- **Algorithmic Complexity Guarantees**:
  - **Score Increment (`ZINCRBY`)**: $\mathcal{O}(\log N)$ time. Directly repositions the node in the Skip List by updating pointer spans.
  - **Rank Retrieval (`ZREVRANK`)**: $\mathcal{O}(\log N)$ time. Sums forward pointer span lengths while walking the Skip List from head to member.
  - **Score Lookup (`ZSCORE`)**: $\mathcal{O}(1)$ time via the internal hash map.
  - **Paginated Slice (`ZREVRANGE`)**: $\mathcal{O}(\log N + M)$ time where $M$ is the count of requested elements.
  - **Cardinality (`ZCARD`)**: $\mathcal{O}(1)$ time.
- **Clean 1-Indexed Human Presentation Invariants**:
  - Redis `ZREVRANK` returns 0-indexed values where `0` is the highest score.
  - Our `LeaderboardService` strictly maps this to 1-indexed rankings (`rank = zrevrank + 1`) across all domain DTOs to eliminate client off-by-one errors.
- **Percentile Calculation Invariant**:
  - Calculates a competitor's performance percentile:
    $$\text{percentile} = \text{round}\left( \frac{\text{total\_players} - \text{rank}}{\text{total\_players}} \times 100, 2 \right) \quad \text{if } \text{total\_players} > 1 \text{ else } 100.0$$
  - Rank 1 out of 100 players yields $99.0\%$ percentile (outperforming 99% of competitors).
  - Rank 1 of 1 yields $100.0\%$.
- **Bounded Pagination Protection**:
  - Enforces `limit: int = Query(ge=1, le=100)` and `offset: int = Query(ge=0)` to prevent unbounded sorted set reads into memory.

---

## 2. Key Code Artifacts

- `app/services/cache_service.py`:
  - Implemented async ZSET primitives: `zadd`, `zincrby`, `zrevrank`, `zscore`, `zrevrange_with_scores`, `zrem`, `zcard`.
  - Aligned keyword arguments with `redis-py` parameter signatures (`name=key, value=member`).
- `app/core/exceptions.py`:
  - Added `PlayerNotFoundException` extending `EntityNotFoundException` (mapped to HTTP 404).
- `app/schemas/leaderboard.py`:
  - Defined Pydantic DTOs: `ScoreUpdateRequest`, `ScoreUpdateResponse`, `LeaderboardEntry`, `PlayerStanding`.
- `app/services/leaderboard_service.py`:
  - Engineered domain `LeaderboardService` managing scoped leaderboards (`leaderboard:{id}`).
  - Implemented `record_score`, `get_top_players`, `get_player_standing`, and helper methods.
- `app/core/dependencies.py`:
  - Added `get_leaderboard_service` dependency provider.
- `app/routers/leaderboard_router.py`:
  - Exposed REST endpoints:
    - `POST /leaderboard/{leaderboard_id}/scores/{player_id}`
    - `GET /leaderboard/{leaderboard_id}/top`
    - `GET /leaderboard/{leaderboard_id}/standing/{player_id}`
- `app/main.py`:
  - Mounted `leaderboard_router`.
- `tests/test_leaderboard_zset.py`:
  - 5 comprehensive test suites covering score ordering, dynamic overtake, percentiles, 10k player Skip List benchmark, and HTTP API validation.

---

## 3. Verification & Quality Gates

- **Test Suite**: 395 passing tests (`pytest tests/ -v` passed in ~35s).
- **Static Typing**: `mypy --strict app tests alembic` passed with 0 errors across 89 files.
- **Linting & Formatting**: `ruff check .` and `ruff format --check .` passed with 0 errors.
