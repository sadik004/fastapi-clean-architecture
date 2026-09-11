# Root Cause Analysis (RCA): Day 84 - Uncoordinated Dual-Writes, Insufficient Funds Overdrafts & Unit of Work Atomic Rollback Failure Modes

## 1. Executive Summary

- **Incident Classification**: Transaction Management, ACID Isolation, Overdraft Protection, Multi-Leg Balance Invariants & Test State Isolation
- **Severity**: Critical (Money Disappearing Mid-Transfer, Negative Balance Overdrafts, Orphaned Ledger Postings, Test State Leaks)
- **Primary Failure Modes**:
  1. **The Dual-Write Partial Commit Problem**: Deducting funds from a source account in one transaction and crediting the recipient in a separate transaction. If a crash, timeout, or network partition occurs between the two operations, funds are permanently deducted from the sender without ever arriving at the destination.
  2. **Post-Mutation Balance Check & Catastrophic Overdrafts**: Inserting transfer postings without verifying cleared balance before writing allows accounts to plunge into negative balances, enabling unauthorized overdrafts and platform insolvency.
  3. **Self-Transfer Circular Ledger Pollution**: Permitting users to transfer funds to the same account results in redundant circular journal entries, distorts business volume metrics, and causes erroneous fee deductions.
  4. **In-Memory UoW Rollback State Pollution in Test Fixtures**: When exceptions were raised in unit tests using `InMemoryUnitOfWork`, mutations made to in-memory dictionaries prior to the failure were not rolled back because in-memory structures lacked transactional rollback semantics, leaking state across test cases.
- **Component Under Analysis**: `app/services/ledger_transfer_service.py`, `app/core/unit_of_work.py`, `app/core/exceptions.py`, `app/core/exception_handlers.py`, `tests/test_ledger_transfers.py`
- **Resolution**:
  - Encapsulated the entire transfer operation (source credit, destination debit, fee debit, and journal entry) within a single atomic Unit of Work (`async with self.uow:`) guaranteeing all-or-nothing execution.
  - Implemented pre-flight cleared balance verification: if `cleared_balance < total_required`, immediately raise `InsufficientFundsException` (HTTP 422) and roll back the transaction with zero database state changed.
  - Added self-transfer validation rejecting identical source and destination accounts (`SelfTransferNotAllowedException`, HTTP 400).
  - Enhanced `InMemoryUnitOfWork` with deep-copy snapshotting on enter and state restoration on rollback.

---

## 2. Problem Statement & Production Symptoms

### 2.1 The Dual-Write Partial Commit Catastrophe
In naive payment implementations:
```python
# CATASTROPHIC ANTI-PATTERN: Sequential Uncoordinated Writes
async def transfer_money(source_id, dest_id, amount):
    await db.execute("INSERT INTO postings (account_id, amount, direction) VALUES (?, ?, 'CREDIT')", [source_id, amount])
    await db.commit() # <--- Source is debited/credited!
    
    # 💥 Server crash, network timeout, or process kill occurs right here!
    
    await db.execute("INSERT INTO postings (account_id, amount, direction) VALUES (?, ?, 'DEBIT')", [dest_id, amount])
    await db.commit()
```
#### Production Symptom:
The sender's money is gone, but the recipient's balance never increases. Customer support is flooded with missing fund claims, and reconciling discrepancies requires manual database patching.

### 2.2 Unchecked Pre-Flight Overdrafts
If balances are checked only after postings are staged, or omitted under the assumption that the user UI validates balances:
```python
# CATASTROPHIC: Staging postings without checking cleared balance
postings = [
    PostingCreateDTO(account_id=source_id, amount=1000, direction=CREDIT),
    PostingCreateDTO(account_id=dest_id, amount=1000, direction=DEBIT),
]
await uow.ledger.create_journal_entry(postings=postings)
```
If the source account only has $50:
$$\text{New Cleared Balance} = 50.0000 - 1000.0000 = -950.0000$$
#### Production Symptom:
A malicious or buggy user rapidly triggers multiple simultaneous transfers, draining money they do not possess, cash out, and leaves the platform holding an uncollectible deficit.

### 2.3 In-Memory Unit of Work Rollback State Leaks in Tests
During testing with `InMemoryUnitOfWork`:
```python
# InMemoryUnitOfWork without snapshot rollback
try:
    await transfer_service.transfer_funds(source, dest, amount=999999)
except InsufficientFundsException:
    pass # Expected
# Next test runs:
accounts = await in_memory_repo.get_all_accounts() # Contains partially modified state!
```
#### Production Symptom:
Flaky test suites where tests pass in isolation but fail when run in batch due to dirty state lingering in memory across test boundaries.

---

## 3. Root Cause Analysis

### 3.1 Lack of Transactional Atomic Context
Operating without an explicit Unit of Work allows individual SQL statements to execute with autocommit or independent transaction lifecycles, violating the Atomicity property of ACID.

### 3.2 Failure to Assert Pre-Conditions Before Side-Effects
In Domain-Driven Design and secure software architecture, business invariants (such as non-negative balance) must be validated **before** any mutating side-effects or domain events are staged.

### 3.3 Test Double Fidelity Mismatch
The test double (`InMemoryUnitOfWork`) did not mirror the rollback behavior of the production implementation (`SqlAlchemyUnitOfWork`). `session.rollback()` in PostgreSQL discards uncommitted WAL entries, but plain Python dictionaries retain mutated references unless explicitly snapshotted.

---

## 4. Corrective Actions & Architectural Remediation

### 4.1 Unit of Work ACID Encapsulation
All transfer logic runs strictly within `async with self.uow:`:
```python
# app/services/ledger_transfer_service.py
async def transfer_funds(
    self,
    source_account_id: uuid.UUID,
    destination_account_id: uuid.UUID,
    amount: Decimal,
    reference_id: str,
    description: str,
    fee_amount: Decimal = Decimal("0.0000"),
    fee_account_id: uuid.UUID | None = None,
) -> FundTransferResponseDTO:
    if source_account_id == destination_account_id:
        raise SelfTransferNotAllowedException("Transfers to the same account are prohibited.")

    total_required = amount + fee_amount

    async with self.uow:
        # Idempotency check
        existing = await self.uow.ledger.get_journal_entry_by_ref(reference_id)
        if existing is not None:
            raise DuplicateReferenceException(f"Reference ID '{reference_id}' has already been processed.")

        # Pre-flight cleared balance verification
        src_debits, src_credits = await self.uow.ledger.get_account_balance_aggregates(source_account_id)
        current_balance = src_debits - src_credits
        if current_balance < total_required:
            raise InsufficientFundsException(
                message=f"Insufficient funds: Balance {current_balance} < Required {total_required}",
                account_id=source_account_id,
                current_balance=current_balance,
                required_amount=total_required,
            )

        # Multi-leg balanced postings
        postings = [
            PostingCreateDTO(account_id=source_account_id, amount=total_required, direction=PostingDirection.CREDIT),
            PostingCreateDTO(account_id=destination_account_id, amount=amount, direction=PostingDirection.DEBIT),
        ]
        if fee_amount > Decimal("0.0000") and fee_account_id is not None:
            postings.append(PostingCreateDTO(account_id=fee_account_id, amount=fee_amount, direction=PostingDirection.DEBIT))

        # Atomic commit
        entry = await self.uow.ledger.create_journal_entry(
            reference_id=reference_id, description=description, postings=postings
        )
        await self.uow.commit()
```

### 4.2 Deep-Copy Snapshotting in `InMemoryUnitOfWork`
Implemented snapshot-based rollback for test doubles:
```python
# app/core/unit_of_work.py
class InMemoryUnitOfWork(UnitOfWorkProtocol):
    async def __aenter__(self) -> InMemoryUnitOfWork:
        self.committed = False
        self.rolled_back = False
        # Take deep-copy snapshot of repository state
        self._snapshot_accounts = copy.deepcopy(self._ledger_repo.accounts)
        self._snapshot_entries = copy.deepcopy(self._ledger_repo.entries)
        return self

    async def rollback(self) -> None:
        self.rolled_back = True
        # Restore snapshot to revert uncommitted mutations
        if hasattr(self, "_snapshot_accounts"):
            self._ledger_repo.accounts = copy.deepcopy(self._snapshot_accounts)
            self._ledger_repo.entries = copy.deepcopy(self._snapshot_entries)
```

---

## 5. Permanent Prevention Rules

1. **Rule 1 (Atomic UoW for Transfers)**: Never execute multi-account monetary transfers across separate transactions. Every posting and the parent journal entry must reside within a single atomic Unit of Work.
2. **Rule 2 (Pre-Flight Cleared Balance Guard)**: Always check cleared balance before creating debit/credit postings. If funds are insufficient, raise `InsufficientFundsException` (HTTP 422) and roll back immediately.
3. **Rule 3 (Prohibit Self-Transfers)**: Always reject transfers where source account equals destination account (`SelfTransferNotAllowedException`, HTTP 400).
4. **Rule 4 (Test Double Rollback Fidelity)**: Ensure in-memory test doubles accurately mirror relational database rollback semantics via snapshotting.
