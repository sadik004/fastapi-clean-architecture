# Root Cause Analysis (RCA): Day 83 - Mutable Balance Column Race Conditions, Zero-Sum Balance Invariants & Floating-Point Drift in Double-Entry Ledgers

## 1. Executive Summary

- **Incident Classification**: Financial Domain Modeling, Data Integrity Invariants, Concurrency Anti-Patterns & Precision Degradation
- **Severity**: Critical (Irreversible Financial Inconsistencies, Double-Spending, Salami-Slicing Rounding Loss, Unreconcilable Ledger State)
- **Primary Failure Modes**:
  1. **The Mutable `balance` Column Race Condition**: Representing account balances as an editable integer or float column (`UPDATE accounts SET balance = balance + 100`) directly invites lost updates and write skew during concurrent transactions. Furthermore, mutable balance updates overwrite historical state, leaving zero forensic auditability for missing funds.
  2. **Single-Entry Mutation & Violation of Conservation of Money**: Single-entry ledger designs create or destroy monetary quantities without an equal, offsetting leg. Without enforcing balanced debit/credit pairs, database or network blips allow money to vanish or appear out of thin air.
  3. **IEEE 754 Floating-Point Rounding Drift**: Representing currencies with binary `float` primitives leads to binary precision truncation (`0.1 + 0.2 != 0.3`), resulting in fractional cent discrepancies that cause reconciliation and auditing to fail.
  4. **Normal Balance Class Inversion**: Conflating asset accounts with liability or equity accounts causes incorrect balance arithmetic (e.g. subtracting credits from liability accounts), corrupting balance sheets and audit metrics.
- **Component Under Analysis**: `app/models/ledger.py`, `app/services/ledger_domain_service.py`, `app/repositories/ledger_repository.py`, `app/schemas/ledger.py`
- **Resolution**:
  - Eliminated the mutable `balance` column entirely from `LedgerAccountModel`; balances are strictly dynamic aggregations over immutable historical postings.
  - Enforced append-only immutability: postings and journal entries are insert-only; updates and deletes are permanently forbidden at the repository level.
  - Implemented the **Zero-Sum Balance Invariant**: every journal entry requires at least 2 posting legs where $\sum \text{Debits} == \sum \text{Credits}$ to exact decimal precision, raising `UnbalancedJournalEntryException` (HTTP 422) on any deviation.
  - Enforced `Numeric(18, 4)` and Python `Decimal` with zero `float` conversion across all models, DTOs, and services.
  - Codified the 5 foundational account classes (`ASSET`, `LIABILITY`, `EQUITY`, `REVENUE`, `EXPENSE`) with strict normal balance arithmetic rules.

---

## 2. Problem Statement & Production Symptoms

### 2.1 The Mutable `balance` Column Race Condition
In naive web and fintech implementations, accounts are typically modeled with a mutable column:
```python
# CATASTROPHIC ANTI-PATTERN: Mutable Balance Column
class Account(Base):
    __tablename__ = "accounts"
    id = Column(UUID, primary_key=True)
    balance = Column(Numeric(18, 2), nullable=False, default=0.0)
```
When two concurrent requests attempt to deposit or withdraw funds simultaneously:
```
Request A (Deposit $50): Read balance ($100) -> Compute $150
Request B (Deposit $30): Read balance ($100) -> Compute $130
Request A: UPDATE accounts SET balance = 150 WHERE id = ... (Commits)
Request B: UPDATE accounts SET balance = 130 WHERE id = ... (Commits - Overwrites Request A!)
```
#### Production Symptom:
$50 is permanently lost from the account. Because the column is simply overwritten, there is no immutable audit trail of what happened, who initiated the change, or why the numbers no longer balance with external bank statements.

### 2.2 IEEE 754 Binary Floating-Point Drift
When systems use native `float` or SQL `REAL` / `FLOAT` for financial values:
```python
total = 0.0
for _ in range(1000):
    total += 0.1
# Expected: 100.0
# Actual: 99.99999999999859
```
#### Production Symptom:
Financial reconciliation jobs running at midnight flag balance discrepancies across thousands of accounts. Sub-cent truncation compounds over millions of transactions, exposing the company to regulatory fines and audit failures.

### 2.3 Unbalanced Journal Entries & Money Creation/Destruction
If a system allows creating a journal entry with a $100 debit on an asset account without a matching $100 credit on another account:
$$\sum \text{Debits} - \sum \text{Credits} = 100.0000 \neq 0$$
#### Production Symptom:
Money is created from thin air. When extracting balance sheets, Assets $\neq$ Liabilities + Equity, violating the fundamental accounting equation.

---

## 3. Root Cause Analysis

### 3.1 Failure to Separate State from Event Log
In financial accounting (Martin Fowler's *Accounting Patterns*), current balance is not a primary fact; it is a **derived view** of immutable historical economic events (postings). Storing balance as a mutable column conflates the derived view with the underlying ground truth.

### 3.2 Inadequate Precision and Type Guarantees
Python's `float` and relational database `FLOAT` types use IEEE 754 binary floating-point representation. Numbers like `0.1` have no finite representation in binary, making exact arithmetic mathematically impossible.

### 3.3 Lack of Domain Invariant Enforcement at Creation Boundaries
Allowing journal entries to be persisted without validating debit/credit parity allows corrupted or partial transaction states to enter the database during network disconnects or process termination.

---

## 4. Corrective Actions & Architectural Remediation

### 4.1 Zero-Balance Column Invariant & Dynamic Aggregation
Removed `balance` from `LedgerAccountModel`. Account balance is computed via dynamic aggregation queries in `LedgerRepositoryProtocol`:
```python
# app/repositories/ledger_repository.py
async def get_account_balance_aggregates(
    self, account_id: uuid.UUID
) -> tuple[Decimal, Decimal]:
    stmt = (
        select(
            func.coalesce(
                func.sum(
                    case((JournalPostingModel.direction == PostingDirection.DEBIT, JournalPostingModel.amount), else_=0)
                ), 0
            ).label("total_debits"),
            func.coalesce(
                func.sum(
                    case((JournalPostingModel.direction == PostingDirection.CREDIT, JournalPostingModel.amount), else_=0)
                ), 0
            ).label("total_credits"),
        )
        .where(JournalPostingModel.account_id == account_id)
    )
    result = await self.session.execute(stmt)
    row = result.one()
    return Decimal(str(row.total_debits)), Decimal(str(row.total_credits))
```

### 4.2 Normal Balance Calculation Rule
Normal balances are strictly enforced based on the 5 canonical account classes:
```python
# app/models/ledger.py & app/services/ledger_domain_service.py
if account.account_type in (AccountType.ASSET, AccountType.EXPENSE):
    # Normal balance: Debit
    cleared_balance = total_debits - total_credits
else:
    # LIABILITY, EQUITY, REVENUE -> Normal balance: Credit
    cleared_balance = total_credits - total_debits
```

### 4.3 Zero-Sum Balance Validation
Enforced in `LedgerDomainService.validate_journal_entry`:
```python
# app/services/ledger_domain_service.py
total_debit = sum(
    p.amount for p in postings if p.direction == PostingDirection.DEBIT
)
total_credit = sum(
    p.amount for p in postings if p.direction == PostingDirection.CREDIT
)

if total_debit != total_credit:
    raise UnbalancedJournalEntryException(
        f"Journal entry '{reference_id}' is unbalanced: "
        f"total_debit={total_debit} != total_credit={total_credit} "
        f"(imbalance: {abs(total_debit - total_credit)})"
    )
```

### 4.4 Fixed-Point Precision Standards
All monetary quantities are modeled with `Numeric(precision=18, scale=4)` in SQLAlchemy and `Decimal` in Python, guaranteeing exact precision down to $0.0001$ units.

---

## 5. Permanent Prevention Rules

1. **Rule 1 (Zero Mutable Balance Columns)**: Never add a mutable `balance` column to account tables in financial applications. Always compute balances dynamically or materialize them into append-only snapshots.
2. **Rule 2 (Zero-Sum Invariant Enforced at Domain Boundary)**: Never write a journal entry to the database without verifying that total debits equal total credits. Any imbalance must immediately raise `UnbalancedJournalEntryException` (HTTP 422).
3. **Rule 3 (Strict Decimal Precision)**: Never use `float` for money. Always use `Decimal` with fixed scale ($0.0001$) and `Numeric(18, 4)`.
4. **Rule 4 (Append-Only Immutability)**: Once a journal posting is inserted into the ledger, it must never be updated or deleted. Corrections must be applied via new reversing journal entries.
