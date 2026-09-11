"""Comprehensive Test Suite for Day 85: Ledger Currency & Money Value Object Architecture.

Validates:
1. Money Value Object immutability, banker's rounding (ROUND_HALF_EVEN), and fixed-point 4-decimal precision.
2. Strict float prohibition raising TypeError on instantiation, multiplication, and division.
3. Currency mismatch defense: arithmetic and comparisons between different ISO currencies raise CurrencyMismatchException.
4. FXConversionService rate validation, conversion calculation, and supported pairs.
5. End-to-end multi-currency 4-leg cross-border ledger transfers via Treasury FX clearing accounts.
6. Diagnostic FX rate inspection endpoint.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import httpx
import pytest

from app.core.exceptions import (
    CurrencyMismatchException,
    InvalidFXRateException,
)
from app.core.money import Money
from app.main import app
from app.services.fx_conversion_service import FXConversionService

# =============================================================================
# 1. Money Value Object Unit Tests
# =============================================================================


def test_money_value_object_arithmetic() -> None:
    """Verify addition, subtraction, multiplication, and division on Money objects."""
    m1 = Money(amount=Decimal("100.0000"), currency="USD")
    m2 = Money(amount=Decimal("50.2500"), currency="USD")

    # Addition
    m3 = m1 + m2
    assert m3.amount == Decimal("150.2500")
    assert m3.currency == "USD"

    # Subtraction
    m4 = m1 - m2
    assert m4.amount == Decimal("49.7500")
    assert m4.currency == "USD"

    # Multiplication by Decimal and int
    m5 = m1 * Decimal("2.5")
    assert m5.amount == Decimal("250.0000")
    m6 = 3 * m1
    assert m6.amount == Decimal("300.0000")

    # Division
    m7 = m1 / 4
    assert m7.amount == Decimal("25.0000")

    # Unary operators
    assert (-m1).amount == Decimal("-100.0000")
    assert abs(-m1).amount == Decimal("100.0000")
    assert (+m1).amount == Decimal("100.0000")

    # Relational comparisons
    assert m2 < m1
    assert m1 > m2
    assert m1 >= m1
    assert m2 <= m1
    assert m1 == Money(Decimal("100.0000"), "USD")


def test_money_currency_mismatch_guard() -> None:
    """Verify that arithmetic or relational comparisons between incompatible currencies raise CurrencyMismatchException."""
    usd = Money(amount=Decimal("10.0000"), currency="USD")
    bdt = Money(amount=Decimal("1200.0000"), currency="BDT")
    eur = Money(amount=Decimal("9.2000"), currency="EUR")

    with pytest.raises(CurrencyMismatchException) as exc_info:
        _ = usd + bdt
    assert "Cannot add Money with different currencies" in str(exc_info.value)
    assert exc_info.value.code == "CURRENCY_MISMATCH"

    with pytest.raises(CurrencyMismatchException):
        _ = usd - eur

    with pytest.raises(CurrencyMismatchException):
        _ = usd < bdt

    with pytest.raises(CurrencyMismatchException):
        _ = eur >= usd

    # Equality check returns False without raising exception
    assert usd != bdt


def test_bankers_rounding_half_even() -> None:
    """Verify Banker's Rounding (ROUND_HALF_EVEN) to eliminate statistical rounding bias."""
    # When rounding to nearest even digit:
    # 1.00025 -> preceding digit 2 is even -> 1.0002
    m_even = Money(amount=Decimal("1.00025"), currency="USD")
    assert m_even.amount == Decimal("1.0002")

    # 1.00035 -> preceding digit 3 is odd -> rounds up to 4 (even)
    m_odd = Money(amount=Decimal("1.00035"), currency="USD")
    assert m_odd.amount == Decimal("1.0004")

    # 1.00045 -> preceding digit 4 is even -> 1.0004
    m_even2 = Money(amount=Decimal("1.00045"), currency="USD")
    assert m_even2.amount == Decimal("1.0004")

    # 1.00055 -> preceding digit 5 is odd -> rounds up to 6 (even)
    m_odd2 = Money(amount=Decimal("1.00055"), currency="USD")
    assert m_odd2.amount == Decimal("1.0006")


def test_money_float_prohibition() -> None:
    """Verify that float numbers are strictly forbidden to prevent IEEE 754 precision drift."""
    # Instantiation with float
    with pytest.raises(TypeError) as exc_info:
        Money(amount=10.50, currency="USD")  # type: ignore[arg-type]
    assert "Float values are strictly forbidden" in str(exc_info.value)

    m = Money(amount=Decimal("100.0000"), currency="USD")

    # Multiplication with float
    with pytest.raises(TypeError) as exc_info:
        _ = m * 1.5  # type: ignore[operator]
    assert "Float multiplication is prohibited" in str(exc_info.value)

    # Division with float
    with pytest.raises(TypeError) as exc_info:
        _ = m / 2.0  # type: ignore[operator]
    assert "Float division is prohibited" in str(exc_info.value)


# =============================================================================
# 2. FXConversionService Unit Tests
# =============================================================================


def test_fx_conversion_service() -> None:
    """Verify FXConversionService rate validation and conversion calculations."""
    service = FXConversionService()
    usd = Money(amount=Decimal("100.0000"), currency="USD")

    # Convert USD -> BDT @ 120.5000
    bdt, rate = service.convert(money=usd, target_currency="BDT", exchange_rate=Decimal("120.5000"))
    assert bdt.currency == "BDT"
    assert bdt.amount == Decimal("12050.0000")
    assert rate == Decimal("120.5000")

    # Same currency conversion short-circuits
    same_m, same_rate = service.convert(money=usd, target_currency="USD", exchange_rate=Decimal("1.0000"))
    assert same_m == usd
    assert same_rate == Decimal("1.0000")

    # Negative exchange rate rejected
    with pytest.raises(InvalidFXRateException) as exc_info:
        service.convert(money=usd, target_currency="BDT", exchange_rate=Decimal("-1.5000"))
    assert "strictly greater than zero" in str(exc_info.value)

    # Zero exchange rate rejected
    with pytest.raises(InvalidFXRateException):
        service.convert(money=usd, target_currency="BDT", exchange_rate=Decimal("0.0000"))

    # Float exchange rate rejected
    with pytest.raises(TypeError):
        service.convert(money=usd, target_currency="BDT", exchange_rate=120.50)  # type: ignore[arg-type]

    # Supported rates dictionary check
    rates = service.get_supported_rates()
    assert "USD_BDT" in rates
    assert rates["USD_BDT"] == Decimal("120.5000")


# =============================================================================
# 3. Multi-Currency Cross-Border Ledger Transfer Integration Tests
# =============================================================================


@pytest.mark.asyncio
async def test_cross_currency_ledger_transfer_e2e() -> None:
    """Verify an end-to-end cross-currency transfer (USD to BDT) via 4-leg Treasury FX Clearing."""
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        # 1. Create USD Source Account
        src_res = await client.post(
            "/api/v1/ledger/accounts",
            json={
                "account_number": f"usd-src-{uuid.uuid4().hex[:8]}",
                "name": "US Remitter Wallet",
                "account_type": "ASSET",
                "currency": "USD",
            },
        )
        assert src_res.status_code == 201
        src = src_res.json()

        # 2. Create BDT Destination Account
        dst_res = await client.post(
            "/api/v1/ledger/accounts",
            json={
                "account_number": f"bdt-dst-{uuid.uuid4().hex[:8]}",
                "name": "BD Beneficiary Wallet",
                "account_type": "ASSET",
                "currency": "BDT",
            },
        )
        assert dst_res.status_code == 201
        dst = dst_res.json()

        # 3. Create USD Vault to fund Source Account
        vault_res = await client.post(
            "/api/v1/ledger/accounts",
            json={
                "account_number": f"usd-vlt-{uuid.uuid4().hex[:8]}",
                "name": "USD Reserve Vault",
                "account_type": "LIABILITY",
                "currency": "USD",
            },
        )
        vault = vault_res.json()

        # Fund Source Account with $1,000.0000 USD
        fund_res = await client.post(
            "/api/v1/ledger/entries",
            json={
                "reference_id": f"fund-usd-{uuid.uuid4().hex}",
                "description": "Initial USD Capital Injection",
                "postings": [
                    {"account_id": src["id"], "amount": "1000.0000", "direction": "DEBIT"},
                    {"account_id": vault["id"], "amount": "1000.0000", "direction": "CREDIT"},
                ],
            },
        )
        assert fund_res.status_code == 201

        # 4. Execute Cross-Border Transfer: $200.0000 USD -> BDT @ rate 120.5000 (Target: 24,100.0000 BDT)
        ref_id = f"remit-tx-{uuid.uuid4().hex}"
        tx_res = await client.post(
            "/api/v1/ledger/transfers",
            json={
                "source_account_id": src["id"],
                "destination_account_id": dst["id"],
                "amount": "200.0000",
                "exchange_rate": "120.5000",
                "reference_id": ref_id,
                "description": "Remittance USD to BDT",
            },
        )
        assert tx_res.status_code == 201, f"Transfer failed: {tx_res.text}"
        tx_data = tx_res.json()

        # Assert response fields
        assert tx_data["transferred_amount"] == "200.0000"
        assert tx_data["source_currency"] == "USD"
        assert tx_data["destination_currency"] == "BDT"
        assert tx_data["exchange_rate"] == "120.5000"
        assert tx_data["destination_amount"] == "24100.0000"
        assert tx_data["source_new_balance"] == "800.0000"
        assert tx_data["destination_new_balance"] == "24100.0000"

        # 5. Verify dynamic balances of user accounts
        src_bal = (await client.get(f"/api/v1/ledger/accounts/{src['id']}/balance")).json()
        dst_bal = (await client.get(f"/api/v1/ledger/accounts/{dst['id']}/balance")).json()
        assert Decimal(str(src_bal["balance"])) == Decimal("800.0000")
        assert Decimal(str(dst_bal["balance"])) == Decimal("24100.0000")

        # 6. Verify 4-leg Journal Entry Structure & Internal Clearing Balance
        entry_res = await client.get(f"/api/v1/ledger/entries/{tx_data['journal_entry_id']}")
        assert entry_res.status_code == 200
        entry = entry_res.json()
        postings = entry["postings"]
        assert len(postings) == 4, f"Expected exactly 4 legs in cross-currency entry, got {len(postings)}"

        # Total Debits == Total Credits across all legs
        total_debits = sum(Decimal(str(p["amount"])) for p in postings if p["direction"] == "DEBIT")
        total_credits = sum(Decimal(str(p["amount"])) for p in postings if p["direction"] == "CREDIT")
        assert total_debits == total_credits
        # Total Debits = $200 (source) + 24,100 (target) = 24,300.0000
        assert total_debits == Decimal("24300.0000")


@pytest.mark.asyncio
async def test_missing_exchange_rate_on_cross_currency_rejected() -> None:
    """Verify that attempting a cross-currency transfer without exchange_rate returns HTTP 422."""
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        src = (
            await client.post(
                "/api/v1/ledger/accounts",
                json={
                    "account_number": f"usd-acc-{uuid.uuid4().hex[:8]}",
                    "name": "USD Account",
                    "account_type": "ASSET",
                    "currency": "USD",
                },
            )
        ).json()
        dst = (
            await client.post(
                "/api/v1/ledger/accounts",
                json={
                    "account_number": f"eur-acc-{uuid.uuid4().hex[:8]}",
                    "name": "EUR Account",
                    "account_type": "ASSET",
                    "currency": "EUR",
                },
            )
        ).json()

        # Fund USD account
        vault = (
            await client.post(
                "/api/v1/ledger/accounts",
                json={
                    "account_number": f"vlt-{uuid.uuid4().hex[:8]}",
                    "name": "Vault",
                    "account_type": "LIABILITY",
                    "currency": "USD",
                },
            )
        ).json()
        await client.post(
            "/api/v1/ledger/entries",
            json={
                "reference_id": f"fund-eur-{uuid.uuid4().hex}",
                "description": "Funding",
                "postings": [
                    {"account_id": src["id"], "amount": "500.0000", "direction": "DEBIT"},
                    {"account_id": vault["id"], "amount": "500.0000", "direction": "CREDIT"},
                ],
            },
        )

        # Attempt cross-currency transfer without exchange_rate
        res = await client.post(
            "/api/v1/ledger/transfers",
            json={
                "source_account_id": src["id"],
                "destination_account_id": dst["id"],
                "amount": "50.0000",
                "reference_id": f"no-fx-{uuid.uuid4().hex}",
                "description": "Transfer without FX rate",
            },
        )
        assert res.status_code == 422
        body = res.json()
        assert "INVALID_FX_RATE" in str(body) or "exchange rate must be provided" in str(body).lower()


@pytest.mark.asyncio
async def test_fx_rates_diagnostic_endpoint() -> None:
    """Verify GET /api/v1/ledger/fx/rates returns supported reference simulation rates."""
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        res = await client.get("/api/v1/ledger/fx/rates")
        assert res.status_code == 200
        rates = res.json()
        assert "USD_BDT" in rates
        assert Decimal(str(rates["USD_BDT"])) == Decimal("120.5000")
