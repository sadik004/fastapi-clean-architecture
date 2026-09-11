"""Fintech Atomic Money Transfer Domain Service.

Guarantees:
1. ACID Isolation & Atomicity: All multi-leg postings execute within an atomic Unit of Work transaction boundary.
2. Non-Negative Balance Invariant: The source account's real-time cleared balance is verified before posting legs.
3. Zero-Sum Balance Invariant: Sum of all debits must strictly equal sum of all credits to 4 decimal places.
4. Multi-Currency 4-Leg Clearing Architecture: Cross-currency transfers execute via Treasury FX clearing accounts.
5. Idempotency Protection: Replayed transaction reference IDs are rejected with HTTP 409 Conflict.
6. Clean Architecture Decoupling: Injects UnitOfWorkProtocol; zero FastAPI or transport layer coupling.
"""

from __future__ import annotations

import logging
from decimal import Decimal
from uuid import UUID

from app.core.exceptions import (
    DuplicateReferenceException,
    InactiveLedgerAccountException,
    InsufficientFundsException,
    InvalidFXRateException,
    LedgerAccountNotFoundException,
    SelfTransferNotAllowedException,
    UnbalancedJournalEntryException,
    ValidationException,
)
from app.core.money import Money
from app.core.protocols import UnitOfWorkProtocol
from app.repositories.ledger_repository import LedgerAccountEntity
from app.schemas.ledger import (
    AccountType,
    FundTransferResponseDTO,
    PostingCreateDTO,
    PostingDirection,
)
from app.services.fx_conversion_service import FXConversionService

logger = logging.getLogger("app.services.ledger_transfer")


class LedgerTransferService:
    """Domain service orchestrating atomic multi-leg financial transfers across ledger accounts."""

    def __init__(
        self,
        uow: UnitOfWorkProtocol,
        fx_service: FXConversionService | None = None,
    ) -> None:
        self.uow = uow
        self.fx_service = fx_service or FXConversionService()

    async def transfer_funds(
        self,
        source_account_id: UUID,
        destination_account_id: UUID,
        amount: Decimal,
        reference_id: str,
        description: str,
        fee_amount: Decimal = Decimal("0.0000"),
        fee_account_id: UUID | None = None,
        exchange_rate: Decimal | None = None,
    ) -> FundTransferResponseDTO:
        """Execute an atomic multi-leg fund transfer satisfying non-negative and zero-sum balance invariants.

        Supports both same-currency (2-leg/3-leg) and cross-currency FX transfers (4-leg/5-leg).

        Posting Structure for Cross-Currency Transfer:
        - Leg 1: Source User Account (Credit source currency total required)
        - Leg 2: Treasury Source FX Clearing Account (Debit source currency amount)
        - Leg 3: Treasury Target FX Clearing Account (Credit target currency converted amount)
        - Leg 4: Destination User Account (Debit target currency converted amount)
        - (Optional Leg 5: Platform Fee Account Debit fee amount in source currency)
        """
        # 1. Pre-flight Validation
        if source_account_id == destination_account_id:
            raise SelfTransferNotAllowedException(
                "Self-transfer is prohibited: Source and destination accounts must be distinct."
            )

        if amount <= Decimal("0.0000"):
            raise ValidationException("Transfer amount must be strictly greater than zero.")

        if fee_amount < Decimal("0.0000"):
            raise ValidationException("Platform fee amount cannot be negative.")

        if fee_amount > Decimal("0.0000") and fee_account_id is None:
            raise ValidationException("Fee account ID must be specified when a platform fee amount is charged.")

        if fee_account_id is not None and (
            fee_account_id == source_account_id or fee_account_id == destination_account_id
        ):
            raise ValidationException("Fee account ID must be distinct from both source and destination accounts.")

        total_required = amount + fee_amount

        # 2. Atomic Execution via Unit of Work
        async with self.uow:
            # Idempotency Check
            existing = await self.uow.ledger.get_journal_entry_by_reference(reference_id)
            if existing is not None:
                raise DuplicateReferenceException(
                    f"A journal transaction with reference ID '{reference_id}' has already been processed."
                )

            # Load and Validate Account States
            source_acc = await self.uow.ledger.get_account_by_id(source_account_id)
            if source_acc is None:
                raise LedgerAccountNotFoundException(f"Source ledger account '{source_account_id}' was not found.")
            if not source_acc.is_active:
                raise InactiveLedgerAccountException(
                    f"Source ledger account '{source_account_id}' is inactive and cannot execute transfers."
                )

            dest_acc = await self.uow.ledger.get_account_by_id(destination_account_id)
            if dest_acc is None:
                raise LedgerAccountNotFoundException(
                    f"Destination ledger account '{destination_account_id}' was not found."
                )
            if not dest_acc.is_active:
                raise InactiveLedgerAccountException(
                    f"Destination ledger account '{destination_account_id}' is inactive and cannot receive transfers."
                )

            if fee_amount > Decimal("0.0000") and fee_account_id is not None:
                fee_acc = await self.uow.ledger.get_account_by_id(fee_account_id)
                if fee_acc is None:
                    raise LedgerAccountNotFoundException(f"Fee ledger account '{fee_account_id}' was not found.")
                if not fee_acc.is_active:
                    raise InactiveLedgerAccountException(
                        f"Fee ledger account '{fee_account_id}' is inactive and cannot collect fees."
                    )

            # Check Currency and Handle FX Conversion
            is_cross_currency = source_acc.currency != dest_acc.currency
            destination_amount = amount
            applied_rate: Decimal | None = None

            if is_cross_currency:
                if exchange_rate is None or exchange_rate <= Decimal("0.0000"):
                    raise InvalidFXRateException(
                        f"Cross-currency transfer from '{source_acc.currency}' to '{dest_acc.currency}' "
                        "requires an explicit exchange rate strictly greater than zero."
                    )
                source_money = Money(amount=amount, currency=source_acc.currency)
                target_money, applied_rate = self.fx_service.convert(
                    money=source_money,
                    target_currency=dest_acc.currency,
                    exchange_rate=exchange_rate,
                )
                destination_amount = target_money.amount

            # Real-Time Source Balance & Non-Negative Invariant Guard
            src_debits, src_credits = await self.uow.ledger.get_account_balance_aggregates(source_account_id)
            if source_acc.account_type in (AccountType.ASSET, AccountType.EXPENSE):
                current_balance = src_debits - src_credits
            else:
                current_balance = src_credits - src_debits

            if current_balance < total_required:
                logger.warning(
                    "Transfer rejected: Insufficient funds on account %s. Balance: %s, Required: %s",
                    source_acc.account_number,
                    current_balance,
                    total_required,
                )
                raise InsufficientFundsException(
                    message=(
                        f"Insufficient funds: Account '{source_acc.account_number}' has cleared balance "
                        f"{current_balance}, but {total_required} is required (amount: {amount}, fee: {fee_amount})."
                    ),
                    account_id=source_account_id,
                    current_balance=current_balance,
                    required_amount=total_required,
                )

            # 3. Multi-Leg Posting Construction
            postings: list[PostingCreateDTO] = []

            if not is_cross_currency:
                # -------------------------------------------------------------
                # Single-Currency Transfer (2-leg or 3-leg)
                # -------------------------------------------------------------
                if source_acc.account_type in (AccountType.ASSET, AccountType.EXPENSE):
                    # Source Asset decreases with CREDIT
                    postings.append(
                        PostingCreateDTO(
                            account_id=source_account_id,
                            amount=total_required,
                            direction=PostingDirection.CREDIT,
                        )
                    )
                    # Destination Asset increases with DEBIT
                    postings.append(
                        PostingCreateDTO(
                            account_id=destination_account_id,
                            amount=amount,
                            direction=PostingDirection.DEBIT,
                        )
                    )
                    # Platform Fee Asset increases with DEBIT
                    if fee_amount > Decimal("0.0000") and fee_account_id is not None:
                        postings.append(
                            PostingCreateDTO(
                                account_id=fee_account_id,
                                amount=fee_amount,
                                direction=PostingDirection.DEBIT,
                            )
                        )
                else:
                    # Source Liability decreases with DEBIT
                    postings.append(
                        PostingCreateDTO(
                            account_id=source_account_id,
                            amount=total_required,
                            direction=PostingDirection.DEBIT,
                        )
                    )
                    # Destination Liability increases with CREDIT
                    postings.append(
                        PostingCreateDTO(
                            account_id=destination_account_id,
                            amount=amount,
                            direction=PostingDirection.CREDIT,
                        )
                    )
                    # Platform Fee Revenue increases with CREDIT
                    if fee_amount > Decimal("0.0000") and fee_account_id is not None:
                        postings.append(
                            PostingCreateDTO(
                                account_id=fee_account_id,
                                amount=fee_amount,
                                direction=PostingDirection.CREDIT,
                            )
                        )
            else:
                # -------------------------------------------------------------
                # Cross-Currency FX Transfer (4-leg or 5-leg)
                # Orchestrates Treasury FX Clearing Accounts for strict isolation
                # -------------------------------------------------------------
                source_fx_acc = await self._get_or_create_treasury_fx_account(source_acc.currency)
                dest_fx_acc = await self._get_or_create_treasury_fx_account(dest_acc.currency)

                # Leg 1: Source User Account (Credit source currency total required for Asset)
                if source_acc.account_type in (AccountType.ASSET, AccountType.EXPENSE):
                    postings.append(
                        PostingCreateDTO(
                            account_id=source_account_id,
                            amount=total_required,
                            direction=PostingDirection.CREDIT,
                        )
                    )
                    # Leg 2: Treasury Source FX Clearing Account (Debit source currency amount)
                    postings.append(
                        PostingCreateDTO(
                            account_id=source_fx_acc.id,
                            amount=amount,
                            direction=PostingDirection.DEBIT,
                        )
                    )
                else:
                    postings.append(
                        PostingCreateDTO(
                            account_id=source_account_id,
                            amount=total_required,
                            direction=PostingDirection.DEBIT,
                        )
                    )
                    postings.append(
                        PostingCreateDTO(
                            account_id=source_fx_acc.id,
                            amount=amount,
                            direction=PostingDirection.CREDIT,
                        )
                    )

                # Leg 3: Treasury Target FX Clearing Account (Credit target currency converted amount for Asset)
                # Leg 4: Destination User Account (Debit target currency converted amount for Asset)
                if dest_acc.account_type in (AccountType.ASSET, AccountType.EXPENSE):
                    postings.append(
                        PostingCreateDTO(
                            account_id=dest_fx_acc.id,
                            amount=destination_amount,
                            direction=PostingDirection.CREDIT,
                        )
                    )
                    postings.append(
                        PostingCreateDTO(
                            account_id=destination_account_id,
                            amount=destination_amount,
                            direction=PostingDirection.DEBIT,
                        )
                    )
                else:
                    postings.append(
                        PostingCreateDTO(
                            account_id=dest_fx_acc.id,
                            amount=destination_amount,
                            direction=PostingDirection.DEBIT,
                        )
                    )
                    postings.append(
                        PostingCreateDTO(
                            account_id=destination_account_id,
                            amount=destination_amount,
                            direction=PostingDirection.CREDIT,
                        )
                    )

                # Optional Leg 5: Platform Fee in Source Currency
                if fee_amount > Decimal("0.0000") and fee_account_id is not None:
                    postings.append(
                        PostingCreateDTO(
                            account_id=fee_account_id,
                            amount=fee_amount,
                            direction=PostingDirection.DEBIT
                            if source_acc.account_type in (AccountType.ASSET, AccountType.EXPENSE)
                            else PostingDirection.CREDIT,
                        )
                    )

            # 4. Zero-Sum Balance Invariant Assertion
            total_debits = sum(
                (p.amount for p in postings if p.direction == PostingDirection.DEBIT),
                Decimal("0.0000"),
            )
            total_credits = sum(
                (p.amount for p in postings if p.direction == PostingDirection.CREDIT),
                Decimal("0.0000"),
            )

            if total_debits != total_credits:
                raise UnbalancedJournalEntryException(
                    f"Zero-sum invariant violated: Debits ({total_debits}) != Credits ({total_credits})",
                    total_debits=total_debits,
                    total_credits=total_credits,
                    imbalance=abs(total_debits - total_credits),
                )

            # 5. Atomic Persistence
            entry = await self.uow.ledger.create_journal_entry(
                reference_id=reference_id,
                description=description,
                postings=postings,
            )
            await self.uow.commit()

            # 6. Post-Commit Dynamic Balances
            new_src_debits, new_src_credits = await self.uow.ledger.get_account_balance_aggregates(source_account_id)
            if source_acc.account_type in (AccountType.ASSET, AccountType.EXPENSE):
                source_new_balance = new_src_debits - new_src_credits
            else:
                source_new_balance = new_src_credits - new_src_debits

            new_dst_debits, new_dst_credits = await self.uow.ledger.get_account_balance_aggregates(
                destination_account_id
            )
            if dest_acc.account_type in (AccountType.ASSET, AccountType.EXPENSE):
                destination_new_balance = new_dst_debits - new_dst_credits
            else:
                destination_new_balance = new_dst_credits - new_dst_debits

            logger.info(
                "Successfully executed atomic transfer %s (ref=%s): %s [%s] -> %s [%s] (amount=%s, fee=%s, fx_rate=%s)",
                entry.id,
                reference_id,
                source_acc.account_number,
                source_acc.currency,
                dest_acc.account_number,
                dest_acc.currency,
                amount,
                fee_amount,
                applied_rate,
            )

            return FundTransferResponseDTO(
                journal_entry_id=entry.id,
                reference_id=entry.reference_id,
                transferred_amount=amount,
                fee_deducted=fee_amount,
                source_new_balance=source_new_balance,
                destination_new_balance=destination_new_balance,
                posted_at=entry.posted_at,
                source_currency=source_acc.currency,
                destination_currency=dest_acc.currency,
                exchange_rate=applied_rate,
                destination_amount=destination_amount,
            )

    async def _get_or_create_treasury_fx_account(self, currency: str) -> LedgerAccountEntity:
        """Resolve or automatically provision a Treasury FX clearing account for the specified currency."""
        clean_curr = currency.strip().upper()
        acc_number = f"TREASURY-FX-{clean_curr}"
        existing = await self.uow.ledger.get_account_by_number(acc_number)
        if existing is not None:
            return existing

        return await self.uow.ledger.create_account(
            account_number=acc_number,
            name=f"Treasury FX Clearing ({clean_curr})",
            account_type=AccountType.LIABILITY,
            currency=clean_curr,
        )
