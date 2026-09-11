"""Fintech Atomic Money Transfer Domain Service.

Guarantees:
1. ACID Isolation & Atomicity: All multi-leg postings execute within an atomic Unit of Work transaction boundary.
2. Non-Negative Balance Invariant: The source account's real-time cleared balance is verified before posting legs.
3. Zero-Sum Balance Invariant: Sum of all debits must strictly equal sum of all credits to 4 decimal places.
4. Idempotency Protection: Replayed transaction reference IDs are rejected with HTTP 409 Conflict.
5. Clean Architecture Decoupling: Injects UnitOfWorkProtocol; zero FastAPI or transport layer coupling.
"""

from __future__ import annotations

import logging
from decimal import Decimal
from uuid import UUID

from app.core.exceptions import (
    DuplicateReferenceException,
    InactiveLedgerAccountException,
    InsufficientFundsException,
    LedgerAccountNotFoundException,
    SelfTransferNotAllowedException,
    UnbalancedJournalEntryException,
    ValidationException,
)
from app.core.protocols import UnitOfWorkProtocol
from app.schemas.ledger import (
    AccountType,
    FundTransferResponseDTO,
    PostingCreateDTO,
    PostingDirection,
)

logger = logging.getLogger("app.services.ledger_transfer")


class LedgerTransferService:
    """Domain service orchestrating atomic multi-leg financial transfers across ledger accounts."""

    def __init__(self, uow: UnitOfWorkProtocol) -> None:
        self.uow = uow

    async def transfer_funds(
        self,
        source_account_id: UUID,
        destination_account_id: UUID,
        amount: Decimal,
        reference_id: str,
        description: str,
        fee_amount: Decimal = Decimal("0.0000"),
        fee_account_id: UUID | None = None,
    ) -> FundTransferResponseDTO:
        """Execute an atomic multi-leg fund transfer satisfying the non-negative and zero-sum balance invariants.

        Posting Structure:
        - Asset-to-Asset:
            Source Asset: CREDIT (amount + fee_amount) -> Balance decreases
            Destination Asset: DEBIT (amount) -> Balance increases
            Fee Asset (if fee > 0): DEBIT (fee_amount) -> Balance increases
            Total Debits (amount + fee_amount) == Total Credits (amount + fee_amount)
        - Liability-to-Liability:
            Source Liability: DEBIT (amount + fee_amount) -> Balance decreases
            Destination Liability: CREDIT (amount) -> Balance increases
            Fee Revenue (if fee > 0): CREDIT (fee_amount) -> Balance increases
            Total Debits (amount + fee_amount) == Total Credits (amount + fee_amount)
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
                "Successfully executed atomic transfer %s (ref=%s): %s -> %s (amount=%s, fee=%s)",
                entry.id,
                reference_id,
                source_acc.account_number,
                dest_acc.account_number,
                amount,
                fee_amount,
            )

            return FundTransferResponseDTO(
                journal_entry_id=entry.id,
                reference_id=entry.reference_id,
                transferred_amount=amount,
                fee_deducted=fee_amount,
                source_new_balance=source_new_balance,
                destination_new_balance=destination_new_balance,
                posted_at=entry.posted_at,
            )
