# Day 83: Fintech Double-Entry Ledger Domain Modeling Architecture (Accounts, Journal Entries, Postings & Zero-Sum Balance Invariants)

## 1. Objective & Architecture Overview
Entering **Phase 8: Capstone Distributed Fintech Double-Entry Ledger System (Days 83–90)**, we engineer an enterprise double-entry ledger architecture modeled after Martin Fowler's Accounting Patterns and canonical banking standards (such as Stripe, Square, and Modern Treasury).

The core engineering objectives:
1. Model the 3 canonical ledger entities (`LedgerAccountModel`, `JournalEntryModel`, `JournalPostingModel`) with UUIDv7 monotonic primary keys.
2. Enforce the **Immutability Invariant**: Ledger postings and journal entries are strictly append-only (zero `UPDATE` or `DELETE` allowed).
3. Enforce the **Zero Mutable Balance Column Invariant**: Ledger accounts never store a mutable `balance` column; balance is always dynamically computed from historical postings.
4. Enforce the **Zero-Sum Balance Invariant**: Every journal entry must have $\ge 2$ legs where $\sum \text{Debits} == \sum \text{Credits}$ to exact decimal precision, or raise `UnbalancedJournalEntryException` (HTTP 422).
5. Enforce **Arbitrary-Precision Arithmetic**: Strict `Numeric(18, 4)` and Python `Decimal` with zero `float` conversion.
6. Strictly adhere to Clean Architecture Rules (1–5).

---

## 2. Real-World Fintech & Banking Analogy
In naive e-commerce codebases, developer teams often execute:
```sql
UPDATE users SET balance = balance + 100 WHERE id = 10;
```
This naive single-entry approach suffers from fatal flaws:
- Money is created out of thin air without an offsetting credit/debit source.
- Concurrent updates trigger race conditions and balance overwrites.
- Zero audit trail exists for financial reconciliation.

In professional fintech double-entry bookkeeping:
When a customer deposits $1,000 into their wallet with a $25 processing fee:
- **Cash Asset (Bank)**: DEBIT $1,000.0000 (Asset increases)
- **Customer Wallet (Liability)**: CREDIT $975.0000 (Liability increases)
- **Fee Revenue (Platform)**: CREDIT $25.0000 (Revenue increases)

Total Debits ($1,000.0000) strictly equals Total Credits ($975.0000 + $25.0000 = $1,000.0000), maintaining the zero-sum conservation of money.

---

## 3. Core Accounting Rules by Account Type
Every account belongs to one of 5 foundational classes:
- **ASSET**: Normal Balance: `DEBIT`. $\text{Balance} = \sum \text{Debits} - \sum \text{Credits}$.
- **EXPENSE**: Normal Balance: `DEBIT`. $\text{Balance} = \sum \text{Debits} - \sum \text{Credits}$.
- **LIABILITY**: Normal Balance: `CREDIT`. $\text{Balance} = \sum \text{Credits} - \sum \text{Debits}$.
- **EQUITY**: Normal Balance: `CREDIT`. $\text{Balance} = \sum \text{Credits} - \sum \text{Debits}$.
- **REVENUE**: Normal Balance: `CREDIT`. $\text{Balance} = \sum \text{Credits} - \sum \text{Debits}$.

---

## 4. Architectural Implementation Breakdown

### 4.1 Relational Schema & Alembic Migration
- `ledger_accounts`: `id` (UUIDv7), `account_number` (Unique index), `name`, `account_type` (Enum), `currency` (3-char ISO), `is_active`, `created_at`.
- `journal_entries`: `id` (UUIDv7), `reference_id` (Unique index), `description`, `posted_at`.
- `journal_postings`: `id` (UUIDv7), `journal_entry_id` (FK to entries), `account_id` (FK to accounts), `amount` (`Numeric(18, 4)`), `direction` (`DEBIT`/`CREDIT`), `created_at`.
- Migration: `alembic/versions/8bbc80ff6f79_create_double_entry_ledger_tables.py`.

### 4.2 Decoupled Clean Layering
- **Protocols** (`app/core/protocols.py`): Re-exports `LedgerRepositoryProtocol`.
- **Repository** (`app/repositories/ledger_repository.py`): Implements `SqlAlchemyLedgerRepository` (aggregating debits/credits via `func.sum(case(...))`) and `InMemoryLedgerRepository`.
- **Service** (`app/services/ledger_domain_service.py`): Injects `LedgerRepositoryProtocol`. Enforces zero-sum check, idempotency check, and normal balance calculations.
- **Router** (`app/routers/ledger_router.py`): Exposes REST endpoints (`/api/v1/ledger/*`). Zero imports from `app.models`.

---

## 5. Verification & Test Suite
`tests/test_double_entry_ledger.py` validates:
- Account creation & dynamic balance lifecycle.
- Unbalanced journal entry rejection with HTTP 422.
- Duplicate reference ID idempotency rejection with HTTP 409.
- 3-leg compound journal entry (Deposit + Fee).
- Arbitrary precision fractional decimal preservation (`0.0001`, `1234.5678`).
- Nonexistent account 404 guards.
- In-memory repository fast unit tests.

All quality gates passed:
- `pytest tests/test_double_entry_ledger.py -v`: 7 passed
- `pytest tests/test_architecture_compliance.py -v`: 10 passed
- `python scripts/audit_architecture.py`: 100% clean architecture verified
- `mypy`: 0 issues found in 11 files
- `ruff check` & `ruff format`: Clean
