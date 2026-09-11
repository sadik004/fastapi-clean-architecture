"""Foreign Exchange (FX) Conversion Engine.

Provides deterministic, arbitrary-precision multi-currency conversion adhering to:
1. Banker's Rounding (ROUND_HALF_EVEN) to 4 decimal places.
2. Strict Float Prohibition (only Decimal permitted).
3. Positive Exchange Rate validation (> 0.0000).
4. ISO 4217 currency pair auditing.
"""

from __future__ import annotations

import logging
import re
from decimal import ROUND_HALF_EVEN, Decimal

from app.core.exceptions import InvalidFXRateException
from app.core.money import Money

logger = logging.getLogger("app.services.fx_conversion")

_ISO_4217_REGEX = re.compile(r"^[A-Z]{3}$")

# Standard reference / simulation rates for currency pairs
DEFAULT_SIMULATION_RATES: dict[str, Decimal] = {
    "USD_BDT": Decimal("120.5000"),
    "BDT_USD": Decimal("0.0083"),
    "USD_EUR": Decimal("0.9200"),
    "EUR_USD": Decimal("1.0870"),
    "USD_GBP": Decimal("0.7850"),
    "GBP_USD": Decimal("1.2739"),
    "EUR_BDT": Decimal("130.9000"),
    "BDT_EUR": Decimal("0.0076"),
}


class FXConversionService:
    """Enterprise FX conversion service for calculating multi-currency ledger conversions."""

    def __init__(self, reference_rates: dict[str, Decimal] | None = None) -> None:
        self._rates = dict(reference_rates or DEFAULT_SIMULATION_RATES)

    def convert(
        self,
        money: Money,
        target_currency: str,
        exchange_rate: Decimal,
    ) -> tuple[Money, Decimal]:
        """Convert a Money value object into a target currency using an audited exchange rate.

        Args:
            money: Source Money value object with amount and source currency.
            target_currency: 3-letter uppercase ISO 4217 target currency code.
            exchange_rate: Multiplicative rate (1 Source = rate Target). Must be strictly > 0.

        Returns:
            Tuple of (converted_money: Money, applied_rate: Decimal).

        Raises:
            TypeError: If exchange_rate is a float.
            InvalidFXRateException: If exchange_rate <= 0.0000.
            ValueError: If target_currency is not a valid 3-letter ISO code.
        """
        # 1. Float Prohibition
        if isinstance(exchange_rate, float):
            raise TypeError("Float exchange rates are strictly prohibited. Pass Decimal to ensure financial precision.")

        # 2. Rate Validation
        if not isinstance(exchange_rate, Decimal):
            try:
                exchange_rate = Decimal(str(exchange_rate))
            except Exception as err:
                raise InvalidFXRateException(f"Invalid exchange rate format: {err}") from err

        if exchange_rate <= Decimal("0.0000"):
            raise InvalidFXRateException(f"Exchange rate must be strictly greater than zero, received: {exchange_rate}")

        # 3. Target Currency Validation
        clean_target = target_currency.strip().upper()
        if not _ISO_4217_REGEX.match(clean_target):
            raise ValueError(
                f"Invalid target ISO 4217 currency code: '{target_currency}'. Must be 3 uppercase alphabetical letters."
            )

        # 4. Same-Currency Short-Circuit
        if money.currency == clean_target:
            return money, Decimal("1.0000")

        # 5. Deterministic Quantized Computation (ROUND_HALF_EVEN)
        converted_raw = money.amount * exchange_rate
        converted_amount = converted_raw.quantize(Decimal("0.0001"), rounding=ROUND_HALF_EVEN)

        converted_money = Money(amount=converted_amount, currency=clean_target)

        logger.info(
            "FX Conversion: %s %s -> %s %s @ rate %s",
            money.amount,
            money.currency,
            converted_amount,
            clean_target,
            exchange_rate,
        )

        return converted_money, exchange_rate

    def get_supported_rates(self) -> dict[str, Decimal]:
        """Retrieve all currently registered reference simulation exchange rates."""
        return dict(self._rates)

    def get_rate(self, source_currency: str, target_currency: str) -> Decimal | None:
        """Lookup simulation exchange rate for a given currency pair."""
        src = source_currency.strip().upper()
        tgt = target_currency.strip().upper()
        if src == tgt:
            return Decimal("1.0000")
        pair_key = f"{src}_{tgt}"
        return self._rates.get(pair_key)
