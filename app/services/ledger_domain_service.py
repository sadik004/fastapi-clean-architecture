"""Fintech Double-Entry Ledger Domain Service.

Business Logic & Domain Invariants:
1. Zero-Sum Balance Invariant: Every journal entry must have >= 2 legs where sum(Debits) == sum(Credits).
2. Pure Precision Decimal Arithmetic: All calculations use Decimal(18, 4) with zero float conversion.
3. Immutability: Postings are append-only. Zero account balance columns.
4. Normal Balance Computation:
   - ASSET & EXPENSE: Normal Balance = DEBIT (Balance = Debits - Credits)
   - LIABILITY, EQUITY & REVENUE: Normal Balance = CREDIT (Balance = Credits - Debits)
5. Decoupled Architecture: Injects LedgerRepositoryProtocol. Zero transport/FastAPI dependencies.
"""

from __future__ import annotations

import logging
from decimal import Decimal
from uuid import UUID

from app.core.exceptions import (
    DuplicateReferenceException,
    EntityConflictException,
    EntityNotFoundException,
    InactiveLedgerAccountException,
    LedgerAccountNotFoundException,
    UnbalancedJournalEntryException,
)
from app.core.protocols import LedgerRepositoryProtocol
from app.repositories.ledger_repository import JournalEntryEntity, LedgerAccountEntity
from app.schemas.ledger import (
    AccountBalanceResponse,
    AccountType,
    JournalEntryCreateDTO,
    JournalEntryResponseDTO,
    LedgerAccountCreate,
    LedgerAccountResponse,
    PostingDirection,
    PostingResponseDTO,
)

logger = logging.getLogger("app.services.ledger")


class LedgerDomainService:
    """Domain service orchestrating double-entry ledger invariant verification and accounting rules."""

    def __init__(self, repo: LedgerRepositoryProtocol) -> None:
        self.repo = repo

    @staticmethod
    def _to_account_response(entity: LedgerAccountEntity) -> LedgerAccountResponse:
        return LedgerAccountResponse(
            id=entity.id,
            account_number=entity.account_number,
            name=entity.name,
            account_type=entity.account_type,
            currency=entity.currency,
            is_active=entity.is_active,
            created_at=entity.created_at,
        )

    @staticmethod
    def _to_entry_response(entity: JournalEntryEntity) -> JournalEntryResponseDTO:
        postings = [
            PostingResponseDTO(
                id=p.id,
                journal_entry_id=p.journal_entry_id,
                account_id=p.account_id,
                amount=p.amount,
                direction=p.direction,
                created_at=p.created_at,
            )
            for p in entity.postings
        ]
        return JournalEntryResponseDTO(
            id=entity.id,
            reference_id=entity.reference_id,
            description=entity.description,
            posted_at=entity.posted_at,
            postings=postings,
        )

    async def create_account(self, dto: LedgerAccountCreate) -> LedgerAccountResponse:
        """Create a new ledger account after verifying account number uniqueness."""
        existing = await self.repo.get_account_by_number(dto.account_number)
        if existing is not None:
            raise EntityConflictException(f"Ledger account with account number '{dto.account_number}' already exists.")

        entity = await self.repo.create_account(
            account_number=dto.account_number,
            name=dto.name,
            account_type=dto.account_type,
            currency=dto.currency,
        )
        logger.info(
            "Created ledger account %s (id=%s, type=%s)",
            entity.account_number,
            entity.id,
            entity.account_type,
        )
        return self._to_account_response(entity)

    async def get_account(self, account_id: UUID) -> LedgerAccountResponse:
        """Retrieve a ledger account by ID."""
        entity = await self.repo.get_account_by_id(account_id)
        if entity is None:
            raise LedgerAccountNotFoundException(f"Ledger account with ID '{account_id}' was not found.")
        return self._to_account_response(entity)

    async def list_accounts(self, limit: int = 100, offset: int = 0) -> list[LedgerAccountResponse]:
        """List ledger accounts with pagination."""
        accounts = await self.repo.list_accounts(limit=limit, offset=offset)
        return [self._to_account_response(a) for a in accounts]

    async def get_journal_entry(self, entry_id: UUID) -> JournalEntryResponseDTO:
        """Retrieve a journal entry by ID."""
        entity = await self.repo.get_journal_entry_by_id(entry_id)
        if entity is None:
            raise EntityNotFoundException(f"Journal entry with ID '{entry_id}' was not found.")
        return self._to_entry_response(entity)

    async def record_journal_entry(self, dto: JournalEntryCreateDTO) -> JournalEntryResponseDTO:
        """Record an atomic multi-leg journal entry satisfying the zero-sum balance invariant.

        Guarantees:
        1. Idempotency: Duplicate reference_id is rejected with 409 Conflict.
        2. Minimum Multi-Leg: At least 2 posting legs required.
        3. Zero-Sum Balance: sum(Debits) == sum(Credits) to exact 4 decimal precision.
        4. Account Validity: All referenced accounts must exist and be active.
        """
        # 1. Idempotency check
        existing = await self.repo.get_journal_entry_by_reference(dto.reference_id)
        if existing is not None:
            raise DuplicateReferenceException(
                f"A journal entry with reference ID '{dto.reference_id}' has already been posted."
            )

        # 2. Multi-leg minimum count invariant
        if len(dto.postings) < 2:
            raise UnbalancedJournalEntryException(
                "A journal entry must contain at least 2 posting legs (minimum one debit and one credit).",
                total_debits=Decimal("0.0000"),
                total_credits=Decimal("0.0000"),
                imbalance=Decimal("0.0000"),
            )

        # 3. Aggregate debits and credits with strict Decimal precision
        total_debits = Decimal("0.0000")
        total_credits = Decimal("0.0000")

        for posting in dto.postings:
            if posting.amount <= Decimal("0.0000"):
                raise UnbalancedJournalEntryException(
                    f"Posting amount must be strictly positive. Got: {posting.amount}"
                )

            if posting.direction == PostingDirection.DEBIT:
                total_debits += posting.amount
            elif posting.direction == PostingDirection.CREDIT:
                total_credits += posting.amount

        # 4. Zero-Sum Balance Invariant enforcement
        imbalance = abs(total_debits - total_credits)
        if total_debits != total_credits:
            logger.warning(
                "Unbalanced journal entry rejected for ref '%s': Debits=%s, Credits=%s, Imbalance=%s",
                dto.reference_id,
                total_debits,
                total_credits,
                imbalance,
            )
            raise UnbalancedJournalEntryException(
                f"Journal entry postings violate zero-sum invariant: total debits ({total_debits}) "
                f"do not equal total credits ({total_credits}). Imbalance: {imbalance}",
                total_debits=total_debits,
                total_credits=total_credits,
                imbalance=imbalance,
            )

        # 5. Account existence and active status verification
        seen_accounts: set[UUID] = set()
        for posting in dto.postings:
            if posting.account_id in seen_accounts:
                continue
            account = await self.repo.get_account_by_id(posting.account_id)
            if account is None:
                raise LedgerAccountNotFoundException(
                    f"Referenced ledger account '{posting.account_id}' does not exist."
                )
            if not account.is_active:
                raise InactiveLedgerAccountException(
                    f"Referenced ledger account '{posting.account_id}' is inactive and cannot accept postings."
                )
            seen_accounts.add(posting.account_id)

        # 6. Atomic persistence of journal entry header and child posting legs
        entry_entity = await self.repo.create_journal_entry(
            reference_id=dto.reference_id,
            description=dto.description,
            postings=dto.postings,
        )
        logger.info(
            "Successfully posted balanced journal entry %s (ref=%s, legs=%d, volume=%s)",
            entry_entity.id,
            entry_entity.reference_id,
            len(entry_entity.postings),
            total_debits,
        )
        return self._to_entry_response(entry_entity)

    async def get_account_balance(self, account_id: UUID) -> AccountBalanceResponse:
        """Dynamically compute the account balance from immutable postings according to normal balance rules.

        Formula:
        - ASSET & EXPENSE: Balance = sum(Debits) - sum(Credits)  [Normal: DEBIT]
        - LIABILITY, EQUITY & REVENUE: Balance = sum(Credits) - sum(Debits)  [Normal: CREDIT]
        """
        account = await self.repo.get_account_by_id(account_id)
        if account is None:
            raise LedgerAccountNotFoundException(f"Ledger account with ID '{account_id}' was not found.")

        total_debits, total_credits = await self.repo.get_account_balance_aggregates(account_id)

        if account.account_type in (AccountType.ASSET, AccountType.EXPENSE):
            balance = total_debits - total_credits
            normal_balance = "DEBIT"
        else:
            balance = total_credits - total_debits
            normal_balance = "CREDIT"

        return AccountBalanceResponse(
            account_id=account.id,
            account_number=account.account_number,
            account_name=account.name,
            account_type=account.account_type,
            currency=account.currency,
            total_debits=total_debits,
            total_credits=total_credits,
            balance=balance,
            normal_balance=normal_balance,
        )
