# Root Cause Analysis (RCA): Day 88 - SQLite Schema Drift on Flagged Entries, Alembic Migration Synchronization & ErrorDetail Envelope Typing

## 1. Executive Summary & Incident Metadata

- **Incident Classification**: Relational Database Schema Drift, Alembic Migration Synchronization, Pydantic Strict Typing & Domain Exception Serialization
- **Severity**: High (Database Operational Errors on Transfer Queries, Mypy Strict Type Incompatibility, Partial Test Regression)
- **Primary Failure Modes**:
  1. **Relational Schema Drift on Existing SQLite Database (`OperationalError: no such column: journal_entries.is_flagged`)**: When introducing the compliance audit field `is_flagged: Mapped[bool]` to SQLAlchemy `JournalEntryModel`, queries issued by `LedgerTransferService` (`SELECT ... journal_entries.is_flagged FROM journal_entries WHERE reference_id = ?`) failed immediately against the existing development and test database (`app.db`). While in-memory test doubles (`InMemoryLedgerRepository`) functioned without error, tests hitting SQLite (`test_ledger_transfers.py`, `test_double_entry_ledger.py`) raised uncaught database exceptions due to missing DDL migration.
  2. **Strict Mypy Type Conflict in Global Error Envelope (`ErrorDetail.details`)**: In `app/core/exception_handlers.py`, when capturing `FraudDetectedException` metadata (`risk_score`, `reasons`), passing a single dictionary `details={"risk_score": ...}` violated the contract of `ErrorDetail.details`, which is statically typed as `list[dict[str, Any]] | None`.
  3. **Module Attribute Export Collision (`get_redis`)**: In `tests/test_fraud_detection.py`, importing `get_redis` from `app.core.dependencies` triggered a Mypy strict error because `app.core.dependencies` consumed `get_redis` internally without re-exporting it in `__all__`.
- **Components Under Analysis**: `app/models/ledger.py`, `alembic/versions/c3d4e5f6a7b8_add_is_flagged_to_journal_entries.py`, `app/core/exception_handlers.py`, `tests/test_fraud_detection.py`
- **Resolution**:
  - Authored and applied Alembic migration `c3d4e5f6a7b8_add_is_flagged_to_journal_entries.py` using `batch_alter_table("journal_entries")` with `server_default=sa.text("0")` to update `app.db` without dropping table data.
  - Formatted `FraudDetectedException` metadata as a list of dictionaries `[{"risk_score": ..., "reasons": ...}]` adhering strictly to `ErrorDetail.details` schema.
  - Cleanly imported `get_redis` from its canonical source `app.core.redis`.

---

## 2. Problem Statement & Production Symptoms

### 2.1 The SQLite Schema Drift Crash
When executing `pytest tests/test_ledger_transfers.py`:
```
sqlalchemy.exc.OperationalError: (sqlite3.OperationalError) no such column: journal_entries.is_flagged
[SQL: SELECT journal_entries.id, journal_entries.reference_id, journal_entries.description, journal_entries.posted_at, journal_entries.is_flagged 
FROM journal_entries 
WHERE journal_entries.reference_id = ?]
[parameters: ('fund-4cbfd0eb0f234c96b4c95419ca546861',)]
```
#### Production Symptom:
Every transaction query attempting to inspect or load journal entries crashed with HTTP 500 or unhandled database driver exceptions, halting transfer processing.

---

### 2.2 ErrorDetail Schema Incompatibility
In `mypy --strict`:
```
app/core/exception_handlers.py:109: error: Argument "details" to "ErrorDetail" has incompatible type "dict[str, Any] | None"; expected "list[dict[str, Any]] | None"  [arg-type]
```
#### Production Symptom:
CI pipeline gate rejected deployment due to type safety violation in the global exception handler envelope.

---

## 3. Root Cause Analysis (5 Whys)

### Track A: Missing Alembic Migration
1. **Why did SQLite raise `no such column: journal_entries.is_flagged`?**  
   Because the SQL query executed by SQLAlchemy included `journal_entries.is_flagged` in its SELECT clause, but the underlying table schema in `app.db` lacked that column.
2. **Why was the column in the SELECT clause?**  
   Because `is_flagged: Mapped[bool]` was added to `JournalEntryModel`.
3. **Why did the underlying database not have the column?**  
   Because DDL schema modifications were not applied to `app.db`.
4. **Why were they not applied automatically?**  
   Because in accordance with enterprise production standards, our FastAPI `lifespan` strictly delegates DDL operations to version-controlled Alembic migrations rather than destructive `Base.metadata.create_all()`.
5. **What was the permanent resolution?**  
   Author a forward-compatible Alembic migration adding `is_flagged` with non-null server defaults (`server_default=sa.text("0")`) and execute `alembic upgrade head`.

---

### Track B: ErrorDetail Envelope Typing
1. **Why did Mypy flag `ErrorDetail` instantiation?**  
   Because `details` was passed a `dict[str, Any]` instead of a `list[dict[str, Any]]`.
2. **Why was a dictionary passed?**  
   Because `FraudDetectedException` carries metadata as key-value pairs (`{"risk_score": ..., "reasons": ...}`).
3. **Why is `ErrorDetail.details` typed as a list?**  
   Because standard API field-level validation errors (such as Pydantic `ValidationError.errors()`) produce a list of error issue objects.
4. **What is the permanent prevention?**  
   Always wrap single-object metadata payloads in a list when populating `ErrorDetail.details` to maintain schema uniformity across validation and domain exceptions.

---

## 4. Architectural & System Implications

```
+-----------------------------------------------------------------------------------+
|                           DOMAIN MODEL DDL LIFECYCLE                              |
+-----------------------------------------------------------------------------------+
                                         |
                                         v
                      +------------------------------------+
                      | Add Field to JournalEntryModel     |
                      | is_flagged: Mapped[bool]           |
                      +------------------------------------+
                                         |
                                         +-----------------------------------+
                                         |                                   |
                                         v                                   v
                      +------------------------------------+   +---------------------------+
                      | ANTI-PATTERN:                      |   | CANONICAL PATTERN:        |
                      | Rely on in-memory models only      |   | Author Alembic Migration  |
                      | (Fails on SQLite / Postgres tables)|   | batch_alter_table.add_col |
                      +------------------------------------+   +---------------------------+
                                         |                                   |
                                         v                                   v
                              OperationalError (500)                 Clean Migration (0-downtime)
```

### Invariants Enforced:
1. **Migration Co-Location Invariant**: Whenever an ORM model in `app/models/` gains or alters a column, an Alembic migration script MUST be authored and committed in the exact same changeset.
2. **Backward-Compatible DDL Invariant**: All new columns added to existing production tables must define `nullable=False` with `server_default` (or `nullable=True`) to prevent lockups or validation crashes on existing rows.
3. **Type-Safe Error Envelope Invariant**: Any structured diagnostic context passed to `ErrorDetail` must conform to `list[dict[str, Any]]`.

---

## 5. Permanent Resolution & Defensive Code Patterns

### 5.1 Alembic Migration Script
(`alembic/versions/c3d4e5f6a7b8_add_is_flagged_to_journal_entries.py`):
```python
def upgrade() -> None:
    """Add is_flagged column to journal_entries table."""
    with op.batch_alter_table("journal_entries", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "is_flagged",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("0"),
                comment="Flag indicating if transaction was flagged by fraud detection engine for compliance review",
            )
        )

def downgrade() -> None:
    """Drop is_flagged column from journal_entries table."""
    with op.batch_alter_table("journal_entries", schema=None) as batch_op:
        batch_op.drop_column("is_flagged")
```

### 5.2 Type-Safe Exception Handler
(`app/core/exception_handlers.py`):
```python
    details: list[dict[str, Any]] | None = None
    if isinstance(exc, FraudDetectedException):
        details = [
            {
                "risk_score": exc.risk_score,
                "reasons": exc.reasons,
            }
        ]

    error_detail = ErrorDetail(
        code=exc.code,
        message=exc.message,
        status_code=status_code,
        timestamp=datetime.now(UTC),
        trace_id=trace_id,
        details=details,
    )
```

---

## 6. Verification Gate & Permanent Guardrails

### 6.1 Test Suite Verification
```bash
pytest tests/test_fraud_detection.py tests/test_double_entry_ledger.py tests/test_ledger_transfers.py -v
```
All 39 Phase 8 ledger tests execute and pass with 100% success rate.

### 6.2 Quality Verification Summary
- **Alembic Database Head**: `c3d4e5f6a7b8` confirmed active on `app.db`.
- **Mypy Strict**: `Success: no issues found in 8 source files`.
- **Ruff Linter & Formatter**: 100% compliant.
- **Architecture Linter**: 100% compliant (0 violations, 0 cycles).
