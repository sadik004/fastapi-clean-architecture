# Root Cause Analysis (RCA): CI/CD Pipeline Failure & Schema Migration Isolation (Days 83–85)

## 1. Executive Summary

- **Incident Classification**: Continuous Integration Pipeline & Database Migration Isolation
- **Severity**: High (GitHub Actions CI Quality Gates blocked on `main`)
- **Primary Failure Modes**:
  1. **Stage 1 (Lint & Format)**: Ruff formatting and import sorting violations in `app/models/ledger.py`.
  2. **Stage 2 (Mypy)**: Missing test dependencies in CI container for Stage 2 static typing, plus missing `__all__` re-exports in `app/models/ledger.py`.
  3. **Stage 5 (Regression Suite)**:
     - Cross-dialect schema drift (`modify_type: NUMERIC -> UUID`) in Alembic tests caused by PostgreSQL-specific `UUID(as_uuid=True)` in SQLite.
     - Outdated migration rollback assertion chain in `tests/test_alembic_migrations.py`.
     - Alembic `env.py` invocation of `fileConfig()` disabling existing loggers (`disable_existing_loggers=True` default), breaking downstream structured logging fixtures.
     - Schema drift detection detecting runtime SQLite partition shards (`audit_logs_y*`, `audit_logs_default`) missing from `Base.metadata`.
     - Clean Architecture domain exception `AuthenticationException` versus legacy `HTTPException` assertion in `test_phase4_security_audit.py`.
- **Resolution**: Aligned ledger models to cross-platform `GUID()`, updated Alembic rollback assertions, set `disable_existing_loggers=False` in `alembic/env.py`, filtered partition shards in `compare_metadata`, and installed complete test harnesses in Stage 2 CI workflow.

---

## 2. Problem Statement & Symptoms

Following the completion of Days 83 through 85, pushing to GitHub triggered the Production CI/CD workflow where 3 stages failed:
1. `Stage 1: Lint & Code Formatting` (Failing after 12s)
2. `Stage 2: Static Type Checking` (Failing after 41s)
3. `Stage 5: Test Suite Regression` (Failing after 35s)
4. `Stage 6: Multi-Stage Container Build Gate` (Skipped due to upstream failures)

---

## 3. Root Cause Analysis (5 Whys)

### Issue 1: Stage 1 Lint & Formatting Failure
- **Why?** Ruff flagged unsorted imports (`I001`) and formatting discrepancies in `app/models/ledger.py`.
- **Fix**: Executed `ruff check --fix` and `ruff format` across all files.

### Issue 2: Stage 2 Mypy Failure
- **Why?** In CI, `mypy --strict app tests alembic scripts` failed with missing imports (`pytest`, `fakeredis`, `locust`).
- **Why were they missing?** The CI YAML only installed `requirements.txt` in Stage 2, omitting test packages required to type-check `tests/`.
- **Fix**: Added test dependencies to Stage 2's pip installation step in `.github/workflows/ci.yml`.

### Issue 3: Cross-Dialect UUID Schema Drift
- **Why did Alembic detect 5 schema drift errors in `test_schema_drift_detection_reports_zero_differences`?**
  `app/models/ledger.py` used `from sqlalchemy.dialects.postgresql import UUID` and `mapped_column(UUID(as_uuid=True))`.
- **Why is that a problem?**
  In SQLite, PostgreSQL `UUID` compiles down to `NUMERIC()`, whereas Alembic compares the declared type with SQLite's reflected type, generating a `modify_type` drift.
- **Fix**: Used the platform-independent `GUID()` TypeDecorator (from `app.models.order`) which persists as `CHAR(36)` in SQLite and native `UUID` in PostgreSQL.

### Issue 4: Alembic Reversibility Test Breakage
- **Why did `test_migration_bidirectional_reversibility` fail?**
  Adding migration `8bbc80ff6f79` (Day 83: Double-Entry Ledger) made it the new `head`. Rolling back `-1` dropped ledger tables, but the test asserted that the previous head (`searchable_products`) was dropped.
- **Fix**: Updated the reversibility test sequence to verify ledger table drop and restoration upon re-upgrade.

### Issue 5: Logging Subsystem Silencing by Alembic `fileConfig`
- **Why did `test_trace_to_log_correlation` capture 0 log records in full test runs?**
  When `tests/test_alembic_migrations.py` executed `apply_migrations()`, Alembic's `env.py` called `fileConfig(config.config_file_name)`.
- **Why did that silence loggers?**
  Python standard library's `fileConfig` defaults to `disable_existing_loggers=True`. This disabled module-level loggers like `logger = get_logger("app.middleware")`, preventing log emissions during subsequent tests.
- **Fix**: Specified `fileConfig(config.config_file_name, disable_existing_loggers=False)` in `alembic/env.py`.

### Issue 6: Partition Shards Detected as Schema Drift
- **Why did `compare_metadata` detect `remove_table` for `audit_logs_y*`?**
  Runtime partition emulation creates SQLite tables for partitioned audit logs. Because partitions are not separate declarative classes in `Base.metadata`, Alembic interpreted them as extraneous removed tables.
- **Fix**: Added an `include_object` hook to `compare_metadata` configuring `MigrationContext` to exclude runtime partition tables.

---

## 4. Verification & Prevention

1. **Local Pipeline Execution**:
   - `ruff check` & `ruff format --check`: 100% PASS (285 files).
   - `mypy --strict app tests alembic scripts`: 100% PASS (284 files, 0 errors).
   - `bandit -r app -ll`: 100% PASS (0 issues).
   - `python scripts/audit_architecture.py`: 100% PASS (5/5 canonical rules verified).
   - `pytest`: 100% PASS (795 passed, 0 failures).

2. **Prevention Policies**:
   - Always use `GUID()` for cross-platform model UUID columns.
   - Always pass `disable_existing_loggers=False` to `fileConfig` in migration scripts.
   - Whenever adding a new migration, update the bidirectional reversibility test suite to assert the new head's tables.
