# RCA: Day 39 - Optimistic Lock rowcount Zero on Dialect Mismatch & Application-Side Version Increment Race

- **Date**: 2026-09-09
- **Trigger**: Two distinct failure modes surfaced during Day 39 implementation and test design:
  1. `rowcount` returning `0` on a visually correct `UPDATE WHERE version=X` query during SQLite integration tests, despite the row existing.
  2. Initial design temptation to compute `version + 1` in Python application code instead of inside the SQL `VALUES()` clause — which would reintroduce a race condition under concurrent load.
- **Faulty Code / Pattern**:
  ```python
  # WRONG: Application-side version increment — introduces race window
  new_version = expected_version + 1   # computed in Python
  stmt = (
      update(UserModel)
      .where(UserModel.id == user_id, UserModel.version == expected_version)
      .values(**update_dict, version=new_version)  # static int, not DB expression
  )
  ```
  And separately, a dialect-unsafe engine configuration:
  ```python
  # WRONG: pool_size on SQLite StaticPool raises TypeError
  engine = create_async_engine(url, pool_size=5, max_overflow=10)
  ```
- **Root Cause**:
  1. **Application-Side Version Increment Race**: Computing `new_version = expected_version + 1` in Python and injecting it as a static integer literal into `VALUES(version=N)` defeats the entire atomicity guarantee. Between the Python read and the SQL write, another coroutine could commit `version=N+1` to the DB. The `WHERE version=N` predicate would still match the stale row if the ORM session cache (`identity map`) is not invalidated. The correct pattern is a DB-side expression: `.values(version=UserModel.version + 1)` which compiles to `SET version = version + 1` — a single atomic read-modify-write inside the database engine's transaction.
  2. **Dialect Pool Mismatch (`rowcount=0` false negative)**: SQLite's `aiosqlite` dialect uses a `StaticPool` (single shared connection) for in-memory test databases. Passing `pool_size` or `max_overflow` kwargs to `create_async_engine` on a `StaticPool` URL raises `TypeError: Invalid argument(s) 'pool_size'`, which silently fell back to a mismatched connection, causing `execute()` to return a result where `rowcount` was unreliable (driver-reported `-1` or `0` depending on DBAPI).
- **Resolution**:
  1. Replaced the application-side integer with the SQLAlchemy column expression for DB-side atomic increment:
     ```python
     # CORRECT: DB-side atomic increment — no race window
     stmt = (
         update(UserModel)
         .where(UserModel.id == user_id, UserModel.version == expected_version)
         .values(**update_dict, version=UserModel.version + 1)
     )
     ```
     This generates: `UPDATE users SET ..., version = version + 1 WHERE id = :id AND version = :ver`
  2. Applied the existing **Dialect-Safe Engine Pool Initialization** pattern (SKILL.md rule #55): conditionally suppress `pool_size`/`max_overflow` for SQLite URLs, preventing `TypeError` and ensuring `rowcount` is populated correctly by the DBAPI cursor.
- **Permanent Prevention Rules**:
  1. **DB-Side Version Increment is Non-Negotiable**: In any OCC implementation, always express the version bump as `Model.version + 1` inside `.values()`. This compiles to a single SQL atomic expression. Never pre-compute the new version in Python and pass a static integer literal.
  2. **`rowcount` as the Sole Conflict Sentinel**: After every OCC `UPDATE`, check `result.rowcount == 0` unconditionally. A zero `rowcount` is the only authoritative signal of either a missing row or a stale version; do not perform a separate `SELECT` to pre-validate the version before the `UPDATE` (that creates a new TOCTOU race window).
  3. **Dialect Pool Kwarg Safety**: Always guard `pool_size`, `max_overflow`, and `pool_timeout` behind a dialect check before passing to `create_async_engine`. SQLite/`StaticPool` dialects reject queue pool arguments and silently corrupt connection state.
