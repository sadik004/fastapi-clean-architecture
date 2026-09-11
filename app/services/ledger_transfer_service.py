"""Fintech Atomic Money Transfer Domain Service with Distributed Locking and Idempotent Ingestion.

Guarantees:
1. ACID Isolation & Atomicity: All multi-leg postings execute within an atomic Unit of Work transaction boundary.
2. Non-Negative Balance Invariant: The source account's real-time cleared balance is verified before posting legs.
3. Zero-Sum Balance Invariant: Sum of all debits must strictly equal sum of all credits to 4 decimal places.
4. Multi-Currency 4-Leg Clearing Architecture: Cross-currency transfers execute via Treasury FX clearing accounts.
5. In-Flight Idempotency State Machine: Replayed transactions return cached responses with zero duplicate DB writes; concurrent duplicate requests fail with HTTP 409.
6. Redlock Distributed Mutex: Lexicographically sorts account locks to eliminate distributed deadlocks.
7. Clean Architecture Decoupling: Injects UnitOfWorkProtocol; zero FastAPI or transport layer coupling.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from decimal import Decimal
from uuid import UUID

from redis.asyncio import Redis

from app.core.distributed_lock import AsyncDistributedLock
from app.core.exceptions import (
    ConcurrentTransferInProgressException,
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
        redis: Redis | None = None,
        lock_manager: AsyncDistributedLock | None = None,
    ) -> None:
        self.uow = uow
        self.fx_service = fx_service or FXConversionService()
        self.redis = redis
        self.lock_manager: AsyncDistributedLock | None
        if lock_manager is not None:
            self.lock_manager = lock_manager
        elif redis is not None:
            self.lock_manager = AsyncDistributedLock(redis)
        else:
            self.lock_manager = None

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
        Protected by Redis distributed mutex and atomic idempotency state machine.
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

        # 2. In-Flight Idempotency State Machine (Redis)
        idempotency_key = f"idempotency:{reference_id}"
        in_flight_locked = False

        if self.redis is not None:
            cached_val = await self.redis.get(idempotency_key)
            if cached_val:
                try:
                    payload = json.loads(cached_val)
                    status_str = payload.get("status")
                    if status_str == "PROCESSING":
                        raise ConcurrentTransferInProgressException(
                            f"A concurrent transfer with reference ID '{reference_id}' is currently in progress.",
                            reference_id=reference_id,
                        )
                    if status_str == "COMPLETED":
                        logger.info(
                            "Idempotent replay intercepted for reference '%s'. Returning cached response.",
                            reference_id,
                        )
                        return FundTransferResponseDTO.model_validate(payload["data"])
                except (json.JSONDecodeError, KeyError) as err:
                    logger.warning("Corrupted idempotency record for '%s': %s", idempotency_key, err)

            # Atomically claim the in-flight reference ID
            acquired_processing = await self.redis.set(
                idempotency_key,
                json.dumps({"status": "PROCESSING"}),
                ex=60,
                nx=True,
            )
            if not acquired_processing:
                raise ConcurrentTransferInProgressException(
                    f"A concurrent transfer with reference ID '{reference_id}' is currently in progress.",
                    reference_id=reference_id,
                )
            in_flight_locked = True

        # 3. Distributed Account Mutex Context
        account_lock_keys = [
            f"account:{source_account_id}",
            f"account:{destination_account_id}",
        ]
        if fee_account_id is not None:
            account_lock_keys.append(f"account:{fee_account_id}")

        if self.lock_manager is not None:
            lock_ctx = self.lock_manager.acquire_multiple(account_lock_keys)
        else:

            @asynccontextmanager
            async def _noop_lock() -> AsyncGenerator[dict[str, str]]:
                yield {}

            lock_ctx = _noop_lock()

        try:
            async with lock_ctx:
                async with self.uow:
                    response = await self._execute_transfer_in_uow(
                        source_account_id=source_account_id,
                        destination_account_id=destination_account_id,
                        amount=amount,
                        reference_id=reference_id,
                        description=description,
                        fee_amount=fee_amount,
                        fee_account_id=fee_account_id,
                        exchange_rate=exchange_rate,
                        total_required=total_required,
                    )

            if self.redis is not None:
                await self.redis.set(
                    idempotency_key,
                    json.dumps(
                        {
                            "status": "COMPLETED",
                            "data": response.model_dump(mode="json"),
                        }
                    ),
                    ex=86400,
                )
            return response
        except Exception:
            if self.redis is not None and in_flight_locked:
                try:
                    curr_val = await self.redis.get(idempotency_key)
                    if curr_val and "PROCESSING" in str(curr_val):
                        await self.redis.delete(idempotency_key)
                except Exception as del_err:
                    logger.error("Failed to clean up in-flight idempotency key '%s': %s", idempotency_key, del_err)
            raise

    async def _execute_transfer_in_uow(
        self,
        source_account_id: UUID,
        destination_account_id: UUID,
        amount: Decimal,
        reference_id: str,
        description: str,
        fee_amount: Decimal,
        fee_account_id: UUID | None,
        exchange_rate: Decimal | None,
        total_required: Decimal,
    ) -> FundTransferResponseDTO:
        """Internal execution within active Unit of Work and distributed locks."""
        # Idempotency Check in DB
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

        # Multi-Leg Posting Construction
        postings: list[PostingCreateDTO] = []

        if not is_cross_currency:
            # Single-Currency Transfer (2-leg or 3-leg)
            if source_acc.account_type in (AccountType.ASSET, AccountType.EXPENSE):
                postings.append(
                    PostingCreateDTO(
                        account_id=source_account_id,
                        amount=total_required,
                        direction=PostingDirection.CREDIT,
                    )
                )
                postings.append(
                    PostingCreateDTO(
                        account_id=destination_account_id,
                        amount=amount,
                        direction=PostingDirection.DEBIT,
                    )
                )
                if fee_amount > Decimal("0.0000") and fee_account_id is not None:
                    postings.append(
                        PostingCreateDTO(
                            account_id=fee_account_id,
                            amount=fee_amount,
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
                        account_id=destination_account_id,
                        amount=amount,
                        direction=PostingDirection.CREDIT,
                    )
                )
                if fee_amount > Decimal("0.0000") and fee_account_id is not None:
                    postings.append(
                        PostingCreateDTO(
                            account_id=fee_account_id,
                            amount=fee_amount,
                            direction=PostingDirection.CREDIT,
                        )
                    )
        else:
            # Cross-Currency FX Transfer (4-leg or 5-leg)
            source_fx_acc = await self._get_or_create_treasury_fx_account(source_acc.currency)
            dest_fx_acc = await self._get_or_create_treasury_fx_account(dest_acc.currency)

            if source_acc.account_type in (AccountType.ASSET, AccountType.EXPENSE):
                postings.append(
                    PostingCreateDTO(
                        account_id=source_account_id,
                        amount=total_required,
                        direction=PostingDirection.CREDIT,
                    )
                )
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

        # Zero-Sum Balance Invariant Assertion
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

        # Atomic Persistence
        entry = await self.uow.ledger.create_journal_entry(
            reference_id=reference_id,
            description=description,
            postings=postings,
        )
        await self.uow.commit()

        # Post-Commit Dynamic Balances
        new_src_debits, new_src_credits = await self.uow.ledger.get_account_balance_aggregates(source_account_id)
        if source_acc.account_type in (AccountType.ASSET, AccountType.EXPENSE):
            source_new_balance = new_src_debits - new_src_credits
        else:
            source_new_balance = new_src_credits - new_src_debits

        new_dst_debits, new_dst_credits = await self.uow.ledger.get_account_balance_aggregates(destination_account_id)
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
