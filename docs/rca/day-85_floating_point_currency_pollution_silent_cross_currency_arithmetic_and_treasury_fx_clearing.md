# Root Cause Analysis (RCA): Day 85 - Floating-Point Currency Pollution, Silent Cross-Currency Arithmetic & Treasury FX Clearing Double-Entry Invariants

## 1. Executive Summary

- **Incident Classification**: Currency Representation, Domain Value Objects, Cross-Border Foreign Exchange & Multi-Currency Ledger Balancing
- **Severity**: Critical (Unbounded Precision Drift, Accidental Cross-Currency Asset Inflation, Unbalanced Multi-Currency Journal Entries)
- **Primary Failure Modes**:
  1. **Silent Cross-Currency Arithmetic Pollution**: Performing direct mathematical operations on numbers representing different currencies (e.g. adding $100 USD directly to 10,000 BDT) without currency conversion. Without domain value object enforcement, Python's dynamic typing treats numbers as plain scalars, producing completely meaningless account totals and catastrophic balance inflation.
  2. **Implicit Float Coercion in Financial Value Objects**: Allowing native Python `float` primitives into monetary calculations introduces binary approximation artifacts (e.g., `0.1 + 0.2 = 0.30000000000000004`). In high-volume ledgers, this causes invisible fractional cent leaks (salami slicing) and balance reconciliation failures.
  3. **Direct Cross-Currency 2-Leg Journal Entry Imbalance**: Attempting to model a cross-currency transfer as a standard 2-leg entry (Debit 12,050 BDT, Credit 100 USD) breaks the fundamental zero-sum accounting invariant ($\sum \text{Debits} == \sum \text{Credits}$) because $100 \neq 12,050$.
  4. **Non-Positive & Unvalidated Exchange Rate Multipliers**: Accepting zero, negative, or unscaled foreign exchange rates produces inverted balances, infinite multipliers, or divide-by-zero crashes.
- **Component Under Analysis**: `app/core/money.py`, `app/services/fx_conversion_service.py`, `app/services/ledger_transfer_service.py`, `app/core/exceptions.py`, `app/core/exception_handlers.py`
- **Resolution**:
  - Engineered an immutable `Money` Value Object (`@dataclass(frozen=True, slots=True)`) with runtime `float` prohibition (`TypeError`), Banker's Rounding (`ROUND_HALF_EVEN`) to 4 decimal places (`0.0001`), and ISO 4217 currency validation.
  - Implemented operator overloading (`+`, `-`, `*`, `/`) strictly guarding against currency mismatch via `CurrencyMismatchException` (HTTP 400).
  - Built `FXConversionService` requiring strictly positive scalar exchange rates (`InvalidFXRateException`, HTTP 422).
  - Implemented the canonical **4-Leg Treasury FX Clearing Double-Entry Pattern**:
    - Leg 1: Source User Account (Credit total required in source currency)
    - Leg 2: Treasury Source FX Clearing Account (Debit principal in source currency)
    - Leg 3: Treasury Destination FX Clearing Account (Credit destination amount in target currency)
    - Leg 4: Destination User Account (Debit destination amount in target currency)
  - Enforced zero-sum balance invariants both globally and within each currency-isolated bucket.

---

## 2. Problem Statement & Production Symptoms

### 2.1 The Cross-Currency Addition Catastrophe
In naive codebases where money is passed as plain floats or Decimals:
```python
# CATASTROPHIC ANTI-PATTERN: Raw Scalar Financial Operations
wallet_usd = 100.00
incoming_payment_bdt = 5000.00

# Developer forgot that incoming payment was in BDT!
wallet_usd += incoming_payment_bdt
# wallet_usd is now 5100.00 USD! 💥
```
#### Production Symptom:
A customer who transferred 5,000 Bangladeshi Taka (~$41 USD) is credited with 5,000 US Dollars. The customer immediately withdraws the funds, costing the company thousands of dollars in unrecoverable losses.

### 2.2 Direct Cross-Currency 2-Leg Transfer Imbalance
If a transfer from USD to BDT is modeled as a simple 2-leg transfer:
```python
# Unbalanced journal entry
postings = [
    PostingCreateDTO(account_id=sadiq_usd_account, amount=Decimal("100.0000"), direction=CREDIT),
    PostingCreateDTO(account_id=abir_bdt_account, amount=Decimal("12050.0000"), direction=DEBIT),
]
# Total Debits: 12050.0000
# Total Credits: 100.0000
# Imbalance: 11950.0000 != 0!
```
#### Production Symptom:
The double-entry ledger's zero-sum invariant check fails with `UnbalancedJournalEntryException` (HTTP 422). If disabled, the entire ledger becomes un-auditable, as the trial balance no longer sums to zero.

### 2.3 The Floating-Point Rounding Leak (Salami Slicing)
```python
cents = 0.0
for _ in range(1_000_000):
    cents += 0.01
# Expected: 10,000.0
# Actual: 10000.000000018848
```
#### Production Symptom:
Daily reconciliation jobs fail because aggregate account balances deviate from bank settlement accounts by small fractional amounts that compound over time.

---

## 3. Root Cause Analysis

### 3.1 Primitive Obsession & Absence of Domain Value Objects
Using primitive data types (`float`, `Decimal`) to represent domain concepts that possess intrinsic attributes (both magnitude *and* currency) violates Domain-Driven Design principles. Scalar quantities cannot enforce currency compatibility at compile time or runtime.

### 3.2 Misunderstanding of Cross-Currency Double-Entry Mechanics
In double-entry bookkeeping, debits and credits cannot cross currency boundaries directly within a single leg. An intermediary clearing account (Treasury FX Clearing) is mathematically necessary to isolate currency denominations while preserving ledger balance.

### 3.3 Asymmetric Rounding Bias
Standard "round half up" rounding introduces an upward statistical bias over large volumes of transactions. Banking systems require Banker's Rounding (`ROUND_HALF_EVEN`), which rounds to the nearest even number on ties, neutralizing bias across the transaction lifecycle.

---

## 4. Corrective Actions & Architectural Remediation

### 4.1 Immutable `Money` Value Object
Created in `app/core/money.py`:
```python
@dataclass(frozen=True, slots=True)
class Money:
    amount: Decimal
    currency: str = "USD"

    def __post_init__(self) -> None:
        if isinstance(self.amount, float):
            raise TypeError("Float values are strictly prohibited for Money. Use Decimal, int, or str.")

        if not isinstance(self.amount, Decimal):
            dec_val = Decimal(str(self.amount))
        else:
            dec_val = self.amount

        # Banker's Rounding to 4 decimal places
        quantized = dec_val.quantize(Decimal("0.0001"), rounding=ROUND_HALF_EVEN)
        object.__setattr__(self, "amount", quantized)

        # ISO 4217 currency validation
        curr = self.currency.strip().upper()
        if not _ISO_4217_REGEX.match(curr):
            raise ValueError(f"Invalid ISO 4217 currency code: '{self.currency}'")
        object.__setattr__(self, "currency", curr)

    def _assert_same_currency(self, other: Money) -> None:
        if self.currency != other.currency:
            raise CurrencyMismatchException(
                f"Cannot operate on mismatched currencies: '{self.currency}' vs '{other.currency}'"
            )

    def __add__(self, other: Money) -> Money:
        if not isinstance(other, Money):
            return NotImplemented
        self._assert_same_currency(other)
        return Money(amount=self.amount + other.amount, currency=self.currency)
```

### 4.2 4-Leg Treasury FX Clearing Double-Entry Pattern
In `LedgerTransferService.transfer_funds`, cross-currency transfers orchestrate 4 legs:
```python
# app/services/ledger_transfer_service.py
# 1. FX conversion
converted_dest = self.fx_service.convert(
    money=Money(amount, src_curr),
    target_currency=dest_curr,
    rate=exchange_rate,
)
dest_amount = converted_dest.amount

# 2. Provision Treasury FX Clearing Accounts
treasury_src_fx = await self._get_or_create_treasury_fx_account(uow, src_curr)
treasury_dest_fx = await self._get_or_create_treasury_fx_account(uow, dest_curr)

# 3. Construct 4-Leg Postings
postings = [
    # Source currency bucket (Sum Debits = Sum Credits = total_required)
    PostingCreateDTO(account_id=source_account_id, amount=total_required, direction=PostingDirection.CREDIT),
    PostingCreateDTO(account_id=treasury_src_fx.id, amount=amount, direction=PostingDirection.DEBIT),
    
    # Destination currency bucket (Sum Debits = Sum Credits = dest_amount)
    PostingCreateDTO(account_id=treasury_dest_fx.id, amount=dest_amount, direction=PostingDirection.CREDIT),
    PostingCreateDTO(account_id=destination_account_id, amount=dest_amount, direction=PostingDirection.DEBIT),
]
```

### 4.3 Positive FX Rate Validation
In `FXConversionService.convert`:
```python
# app/services/fx_conversion_service.py
if rate is not None:
    if isinstance(rate, float):
        raise TypeError("Exchange rate must be a Decimal, str, or int; float is strictly prohibited.")
    dec_rate = Decimal(str(rate))
    if dec_rate <= Decimal("0"):
        raise InvalidFXRateException(f"Exchange rate must be strictly positive (> 0), got: {dec_rate}")
```

---

## 5. Permanent Prevention Rules

1. **Rule 1 (Use Money Value Object)**: Never pass raw floats or untyped numbers for monetary balances. Always encapsulate amounts in the immutable `Money` Value Object.
2. **Rule 2 (Prohibit Direct Cross-Currency Arithmetic)**: Never allow mathematical operations between different currencies without routing through `FXConversionService`. Mismatches must raise `CurrencyMismatchException` (HTTP 400).
3. **Rule 3 (4-Leg Treasury FX Double-Entry Invariant)**: Always bridge cross-currency transfers using Treasury FX Clearing Accounts. Direct 2-leg transfers across disparate currencies are strictly forbidden.
4. **Rule 4 (Banker's Rounding Mandatory)**: Always apply Banker's Rounding (`ROUND_HALF_EVEN`) to 4 decimal places (`0.0001`) on all financial rounding operations.
