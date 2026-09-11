"""Immutable Money Value Object Engine.

Adheres strictly to Martin Fowler's Money Pattern and ISO 4217 currency specifications:
1. Rejects IEEE 754 floating-point numbers with TypeError to eliminate precision drift.
2. Normalizes all quantities to 4 decimal places (0.0001) using Banker's Rounding (ROUND_HALF_EVEN).
3. Enforces strict currency isolation: Arithmetic across mismatched currencies raises CurrencyMismatchException.
4. Implements operator overloading for deterministic fixed-point financial computation.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Decimal

from app.core.exceptions import CurrencyMismatchException

_ISO_4217_REGEX = re.compile(r"^[A-Z]{3}$")


@dataclass(frozen=True, slots=True)
class Money:
    """Immutable, slotted Value Object representing a monetary amount in a specific currency."""

    amount: Decimal
    currency: str = "USD"

    def __post_init__(self) -> None:
        # 1. Strict Float Prohibition
        # Check both amount type and check if float was supplied
        if isinstance(self.amount, float):
            raise TypeError(
                "Float values are strictly forbidden for Money to eliminate precision drift. "
                "Instantiate with Decimal, int, or str."
            )

        # 2. Currency Code Validation & Normalization
        if not isinstance(self.currency, str):
            raise TypeError("Currency code must be a string.")

        clean_currency = self.currency.strip().upper()
        if not _ISO_4217_REGEX.match(clean_currency):
            raise ValueError(
                f"Invalid ISO 4217 currency code: '{self.currency}'. "
                f"Currency must be exactly 3 uppercase alphabetical characters."
            )

        # 3. Decimal Quantization using Banker's Rounding (ROUND_HALF_EVEN)
        try:
            raw_dec = Decimal(str(self.amount)) if not isinstance(self.amount, Decimal) else self.amount
        except Exception as err:
            raise TypeError(f"Cannot convert amount '{self.amount}' to Decimal: {err}") from err

        quantized_amount = raw_dec.quantize(Decimal("0.0001"), rounding=ROUND_HALF_EVEN)

        object.__setattr__(self, "amount", quantized_amount)
        object.__setattr__(self, "currency", clean_currency)

    # -------------------------------------------------------------------------
    # Operator Overloading (Arithmetic)
    # -------------------------------------------------------------------------

    def __add__(self, other: object) -> Money:
        if not isinstance(other, Money):
            return NotImplemented
        if self.currency != other.currency:
            raise CurrencyMismatchException(
                f"Cannot add Money with different currencies: '{self.currency}' and '{other.currency}'. "
                "Cross-currency arithmetic requires explicit Foreign Exchange (FX) conversion."
            )
        return Money(amount=self.amount + other.amount, currency=self.currency)

    def __sub__(self, other: object) -> Money:
        if not isinstance(other, Money):
            return NotImplemented
        if self.currency != other.currency:
            raise CurrencyMismatchException(
                f"Cannot subtract Money with different currencies: '{self.currency}' and '{other.currency}'. "
                "Cross-currency arithmetic requires explicit Foreign Exchange (FX) conversion."
            )
        return Money(amount=self.amount - other.amount, currency=self.currency)

    def __mul__(self, factor: Decimal | int | str) -> Money:
        if isinstance(factor, float):
            raise TypeError("Float multiplication is prohibited on Money objects. Use Decimal or int.")
        if not isinstance(factor, Decimal | int | str):
            return NotImplemented
        dec_factor = Decimal(str(factor))
        return Money(amount=self.amount * dec_factor, currency=self.currency)

    def __rmul__(self, factor: Decimal | int | str) -> Money:
        return self.__mul__(factor)

    def __truediv__(self, divisor: Decimal | int | str) -> Money:
        """Divide monetary amount by an arbitrary-precision scalar."""
        if isinstance(divisor, float):
            raise TypeError("Float division is prohibited on Money objects. Use Decimal or int.")
        if not isinstance(divisor, Decimal | int | str):
            return NotImplemented
        dec_divisor = Decimal(str(divisor))
        if dec_divisor == Decimal("0"):
            raise ZeroDivisionError("Cannot divide Money amount by zero.")
        divided_amount = (self.amount / dec_divisor).quantize(Decimal("0.0001"), rounding=ROUND_HALF_EVEN)
        return Money(amount=divided_amount, currency=self.currency)

    def __neg__(self) -> Money:
        return Money(amount=-self.amount, currency=self.currency)

    def __pos__(self) -> Money:
        return self

    def __abs__(self) -> Money:
        return Money(amount=abs(self.amount), currency=self.currency)

    # -------------------------------------------------------------------------
    # Operator Overloading (Comparisons)
    # -------------------------------------------------------------------------

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Money):
            return False
        return self.currency == other.currency and self.amount == other.amount

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, Money):
            return NotImplemented
        self._assert_same_currency(other)
        return self.amount < other.amount

    def __le__(self, other: object) -> bool:
        if not isinstance(other, Money):
            return NotImplemented
        self._assert_same_currency(other)
        return self.amount <= other.amount

    def __gt__(self, other: object) -> bool:
        if not isinstance(other, Money):
            return NotImplemented
        self._assert_same_currency(other)
        return self.amount > other.amount

    def __ge__(self, other: object) -> bool:
        if not isinstance(other, Money):
            return NotImplemented
        self._assert_same_currency(other)
        return self.amount >= other.amount

    def _assert_same_currency(self, other: Money) -> None:
        if self.currency != other.currency:
            raise CurrencyMismatchException(
                f"Cannot compare Money with different currencies: '{self.currency}' and '{other.currency}'. "
                "Comparison across currencies is undefined without an explicit exchange rate."
            )

    # -------------------------------------------------------------------------
    # Domain Predicates & String Representations
    # -------------------------------------------------------------------------

    @property
    def is_zero(self) -> bool:
        """Return True if the monetary amount is strictly zero."""
        return self.amount == Decimal("0.0000")

    @property
    def is_positive(self) -> bool:
        """Return True if the monetary amount is strictly greater than zero."""
        return self.amount > Decimal("0.0000")

    @property
    def is_negative(self) -> bool:
        """Return True if the monetary amount is strictly less than zero."""
        return self.amount < Decimal("0.0000")

    def __str__(self) -> str:
        return f"{self.amount} {self.currency}"

    def __repr__(self) -> str:
        return f"Money(amount=Decimal('{self.amount}'), currency='{self.currency}')"
