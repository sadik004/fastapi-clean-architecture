"""Fintech Double-Entry Ledger Repository Implementation.

Guarantees:
1. Zero ORM Leakage: Models never leave this repository. They are strictly converted
   into detached domain entities (`LedgerAccountEntity`, `JournalEntryEntity`, `JournalPostingEntity`).
2. Immutability Invariant: Zero update/delete methods. All postings are strictly append-only.
3. Monotonic B-Tree Clustering: All entities use UUIDv7 primary keys.
4. Dual-Mode Repositories: Provides both `SqlAlchemyLedgerRepository` and `InMemoryLedgerRepository`.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Protocol

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.identifiers import generate_uuidv7
from app.models.ledger import (
    JournalEntryModel,
    JournalPostingModel,
    LedgerAccountModel,
)
from app.schemas.ledger import AccountType, PostingCreateDTO, PostingDirection


@dataclass(slots=True)
class LedgerAccountEntity:
    """Pure domain entity representing a ledger account."""

    id: uuid.UUID
    account_number: str
    name: str
    account_type: AccountType
    currency: str
    is_active: bool
    created_at: datetime


@dataclass(slots=True)
class JournalPostingEntity:
    """Pure domain entity representing an individual posting leg."""

    id: uuid.UUID
    journal_entry_id: uuid.UUID
    account_id: uuid.UUID
    amount: Decimal
    direction: PostingDirection
    created_at: datetime


@dataclass(slots=True)
class JournalEntryEntity:
    """Pure domain entity representing a journal entry with all child posting legs."""

    id: uuid.UUID
    reference_id: str
    description: str
    posted_at: datetime
    postings: list[JournalPostingEntity] = field(default_factory=list)
    is_flagged: bool = False


class LedgerRepositoryProtocol(Protocol):
    """Abstract protocol defining ledger persistence and query contracts."""

    async def create_account(
        self,
        account_number: str,
        name: str,
        account_type: AccountType,
        currency: str = "USD",
        account_id: uuid.UUID | None = None,
    ) -> LedgerAccountEntity:
        """Create and persist a new ledger account."""
        ...

    async def get_account_by_id(self, account_id: uuid.UUID) -> LedgerAccountEntity | None:
        """Retrieve a ledger account by primary key ID."""
        ...

    async def get_account_by_number(self, account_number: str) -> LedgerAccountEntity | None:
        """Retrieve a ledger account by its unique account number."""
        ...

    async def list_accounts(self, limit: int = 100, offset: int = 0) -> Sequence[LedgerAccountEntity]:
        """List ledger accounts with pagination."""
        ...

    async def get_journal_entry_by_reference(self, reference_id: str) -> JournalEntryEntity | None:
        """Retrieve a journal entry by its unique idempotency reference ID."""
        ...

    async def get_journal_entries_by_references(self, reference_ids: Sequence[str]) -> Sequence[JournalEntryEntity]:
        """Retrieve multiple journal entries matching reference IDs with child postings."""
        ...

    async def get_journal_entry_by_id(self, entry_id: uuid.UUID) -> JournalEntryEntity | None:
        """Retrieve a journal entry by its primary key ID with all posting legs."""
        ...

    async def create_journal_entry(
        self,
        reference_id: str,
        description: str,
        postings: Sequence[PostingCreateDTO],
        posted_at: datetime | None = None,
        entry_id: uuid.UUID | None = None,
        is_flagged: bool = False,
    ) -> JournalEntryEntity:
        """Persist a journal entry header and all child posting legs atomically."""
        ...

    async def get_postings_by_account_id(self, account_id: uuid.UUID) -> Sequence[JournalPostingEntity]:
        """Retrieve all posting legs associated with a ledger account."""
        ...

    async def get_account_balance_aggregates(self, account_id: uuid.UUID) -> tuple[Decimal, Decimal]:
        """Compute aggregated (total_debits, total_credits) across all immutable postings."""
        ...


class SqlAlchemyLedgerRepository:
    """Asynchronous SQLAlchemy 2.0 Ledger repository implementation."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    @staticmethod
    def _account_to_entity(model: LedgerAccountModel) -> LedgerAccountEntity:
        return LedgerAccountEntity(
            id=model.id,
            account_number=model.account_number,
            name=model.name,
            account_type=AccountType(
                model.account_type.value if hasattr(model.account_type, "value") else str(model.account_type)
            ),
            currency=model.currency,
            is_active=model.is_active,
            created_at=model.created_at,
        )

    @staticmethod
    def _posting_to_entity(model: JournalPostingModel) -> JournalPostingEntity:
        direction_val = model.direction.value if hasattr(model.direction, "value") else str(model.direction)
        return JournalPostingEntity(
            id=model.id,
            journal_entry_id=model.journal_entry_id,
            account_id=model.account_id,
            amount=Decimal(str(model.amount)),
            direction=PostingDirection(direction_val),
            created_at=model.created_at,
        )

    @classmethod
    def _entry_to_entity(cls, model: JournalEntryModel) -> JournalEntryEntity:
        posting_entities = [cls._posting_to_entity(p) for p in (model.postings or [])]
        return JournalEntryEntity(
            id=model.id,
            reference_id=model.reference_id,
            description=model.description,
            posted_at=model.posted_at,
            postings=posting_entities,
            is_flagged=getattr(model, "is_flagged", False),
        )

    async def create_account(
        self,
        account_number: str,
        name: str,
        account_type: AccountType,
        currency: str = "USD",
        account_id: uuid.UUID | None = None,
    ) -> LedgerAccountEntity:
        """Create and persist a new ledger account."""
        now = datetime.now(UTC)
        account_uuid = account_id or generate_uuidv7()
        model = LedgerAccountModel(
            id=account_uuid,
            account_number=account_number,
            name=name,
            account_type=account_type,
            currency=currency.upper(),
            is_active=True,
            created_at=now,
        )
        self.session.add(model)
        await self.session.flush()
        return self._account_to_entity(model)

    async def get_account_by_id(self, account_id: uuid.UUID) -> LedgerAccountEntity | None:
        """Retrieve a ledger account by primary key ID."""
        stmt = select(LedgerAccountModel).where(LedgerAccountModel.id == account_id)
        result = await self.session.execute(stmt)
        model = result.scalar_one_or_none()
        return self._account_to_entity(model) if model else None

    async def get_account_by_number(self, account_number: str) -> LedgerAccountEntity | None:
        """Retrieve a ledger account by unique account number."""
        stmt = select(LedgerAccountModel).where(LedgerAccountModel.account_number == account_number)
        result = await self.session.execute(stmt)
        model = result.scalar_one_or_none()
        return self._account_to_entity(model) if model else None

    async def list_accounts(self, limit: int = 100, offset: int = 0) -> Sequence[LedgerAccountEntity]:
        """List ledger accounts with pagination."""
        stmt = select(LedgerAccountModel).order_by(LedgerAccountModel.created_at.desc()).limit(limit).offset(offset)
        result = await self.session.execute(stmt)
        return [self._account_to_entity(m) for m in result.scalars().all()]

    async def get_journal_entry_by_reference(self, reference_id: str) -> JournalEntryEntity | None:
        """Retrieve a journal entry by idempotency reference ID with postings loaded."""
        stmt = (
            select(JournalEntryModel)
            .options(selectinload(JournalEntryModel.postings))
            .where(JournalEntryModel.reference_id == reference_id)
        )
        result = await self.session.execute(stmt)
        model = result.scalar_one_or_none()
        return self._entry_to_entity(model) if model else None

    async def get_journal_entries_by_references(self, reference_ids: Sequence[str]) -> Sequence[JournalEntryEntity]:
        """Retrieve multiple journal entries matching reference IDs with postings loaded."""
        if not reference_ids:
            return []
        stmt = (
            select(JournalEntryModel)
            .options(selectinload(JournalEntryModel.postings))
            .where(JournalEntryModel.reference_id.in_(reference_ids))
        )
        result = await self.session.execute(stmt)
        models = result.scalars().all()
        return [self._entry_to_entity(m) for m in models]

    async def get_journal_entry_by_id(self, entry_id: uuid.UUID) -> JournalEntryEntity | None:
        """Retrieve a journal entry by primary key ID with postings loaded."""
        stmt = (
            select(JournalEntryModel)
            .options(selectinload(JournalEntryModel.postings))
            .where(JournalEntryModel.id == entry_id)
        )
        result = await self.session.execute(stmt)
        model = result.scalar_one_or_none()
        return self._entry_to_entity(model) if model else None

    async def create_journal_entry(
        self,
        reference_id: str,
        description: str,
        postings: Sequence[PostingCreateDTO],
        posted_at: datetime | None = None,
        entry_id: uuid.UUID | None = None,
        is_flagged: bool = False,
    ) -> JournalEntryEntity:
        """Persist a journal entry header and child posting legs atomically."""
        now = posted_at or datetime.now(UTC)
        entry_uuid = entry_id or generate_uuidv7()

        entry_model = JournalEntryModel(
            id=entry_uuid,
            reference_id=reference_id,
            description=description,
            posted_at=now,
            is_flagged=is_flagged,
        )
        self.session.add(entry_model)
        await self.session.flush()

        posting_entities: list[JournalPostingEntity] = []
        for p in postings:
            posting_uuid = generate_uuidv7()
            posting_model = JournalPostingModel(
                id=posting_uuid,
                journal_entry_id=entry_uuid,
                account_id=p.account_id,
                amount=p.amount,
                direction=p.direction.value,
                created_at=now,
            )
            self.session.add(posting_model)
            posting_entities.append(
                JournalPostingEntity(
                    id=posting_uuid,
                    journal_entry_id=entry_uuid,
                    account_id=p.account_id,
                    amount=p.amount,
                    direction=p.direction,
                    created_at=now,
                )
            )

        await self.session.flush()
        return JournalEntryEntity(
            id=entry_uuid,
            reference_id=reference_id,
            description=description,
            posted_at=now,
            postings=posting_entities,
            is_flagged=is_flagged,
        )

    async def get_postings_by_account_id(self, account_id: uuid.UUID) -> Sequence[JournalPostingEntity]:
        """Retrieve all posting legs associated with a ledger account."""
        stmt = (
            select(JournalPostingModel)
            .where(JournalPostingModel.account_id == account_id)
            .order_by(JournalPostingModel.created_at.asc())
        )
        result = await self.session.execute(stmt)
        return [self._posting_to_entity(p) for p in result.scalars().all()]

    async def get_account_balance_aggregates(self, account_id: uuid.UUID) -> tuple[Decimal, Decimal]:
        """Compute aggregated (total_debits, total_credits) across all immutable postings."""
        stmt = select(
            func.coalesce(
                func.sum(
                    case(
                        (JournalPostingModel.direction == PostingDirection.DEBIT.value, JournalPostingModel.amount),
                        else_=Decimal("0.0000"),
                    )
                ),
                Decimal("0.0000"),
            ).label("total_debits"),
            func.coalesce(
                func.sum(
                    case(
                        (JournalPostingModel.direction == PostingDirection.CREDIT.value, JournalPostingModel.amount),
                        else_=Decimal("0.0000"),
                    )
                ),
                Decimal("0.0000"),
            ).label("total_credits"),
        ).where(JournalPostingModel.account_id == account_id)

        result = await self.session.execute(stmt)
        row = result.one()
        total_debits = Decimal(str(row[0])) if row[0] is not None else Decimal("0.0000")
        total_credits = Decimal(str(row[1])) if row[1] is not None else Decimal("0.0000")
        return total_debits, total_credits


class InMemoryLedgerRepository:
    """In-memory Ledger repository implementing LedgerRepositoryProtocol with O(1) lookups."""

    def __init__(self) -> None:
        self._accounts: dict[uuid.UUID, LedgerAccountEntity] = {}
        self._accounts_by_number: dict[str, uuid.UUID] = {}
        self._entries: dict[uuid.UUID, JournalEntryEntity] = {}
        self._entries_by_ref: dict[str, uuid.UUID] = {}
        self._postings: list[JournalPostingEntity] = []

    async def create_account(
        self,
        account_number: str,
        name: str,
        account_type: AccountType,
        currency: str = "USD",
        account_id: uuid.UUID | None = None,
    ) -> LedgerAccountEntity:
        account_uuid = account_id or generate_uuidv7()
        entity = LedgerAccountEntity(
            id=account_uuid,
            account_number=account_number,
            name=name,
            account_type=account_type,
            currency=currency.upper(),
            is_active=True,
            created_at=datetime.now(UTC),
        )
        self._accounts[account_uuid] = entity
        self._accounts_by_number[account_number] = account_uuid
        return entity

    async def get_account_by_id(self, account_id: uuid.UUID) -> LedgerAccountEntity | None:
        return self._accounts.get(account_id)

    async def get_account_by_number(self, account_number: str) -> LedgerAccountEntity | None:
        account_id = self._accounts_by_number.get(account_number)
        return self._accounts.get(account_id) if account_id else None

    async def list_accounts(self, limit: int = 100, offset: int = 0) -> Sequence[LedgerAccountEntity]:
        all_accounts = sorted(self._accounts.values(), key=lambda a: a.created_at, reverse=True)
        return all_accounts[offset : offset + limit]

    async def get_journal_entry_by_reference(self, reference_id: str) -> JournalEntryEntity | None:
        entry_id = self._entries_by_ref.get(reference_id)
        return self._entries.get(entry_id) if entry_id else None

    async def get_journal_entries_by_references(self, reference_ids: Sequence[str]) -> Sequence[JournalEntryEntity]:
        res: list[JournalEntryEntity] = []
        for ref in reference_ids:
            entry_id = self._entries_by_ref.get(ref)
            if entry_id and entry_id in self._entries:
                res.append(self._entries[entry_id])
        return res

    async def get_journal_entry_by_id(self, entry_id: uuid.UUID) -> JournalEntryEntity | None:
        return self._entries.get(entry_id)

    async def create_journal_entry(
        self,
        reference_id: str,
        description: str,
        postings: Sequence[PostingCreateDTO],
        posted_at: datetime | None = None,
        entry_id: uuid.UUID | None = None,
        is_flagged: bool = False,
    ) -> JournalEntryEntity:
        now = posted_at or datetime.now(UTC)
        entry_uuid = entry_id or generate_uuidv7()

        posting_entities: list[JournalPostingEntity] = []
        for p in postings:
            posting_uuid = generate_uuidv7()
            pe = JournalPostingEntity(
                id=posting_uuid,
                journal_entry_id=entry_uuid,
                account_id=p.account_id,
                amount=p.amount,
                direction=p.direction,
                created_at=now,
            )
            posting_entities.append(pe)
            self._postings.append(pe)

        entry_entity = JournalEntryEntity(
            id=entry_uuid,
            reference_id=reference_id,
            description=description,
            posted_at=now,
            postings=posting_entities,
            is_flagged=is_flagged,
        )
        self._entries[entry_uuid] = entry_entity
        self._entries_by_ref[reference_id] = entry_uuid
        return entry_entity

    async def get_postings_by_account_id(self, account_id: uuid.UUID) -> Sequence[JournalPostingEntity]:
        return [p for p in self._postings if p.account_id == account_id]

    async def get_account_balance_aggregates(self, account_id: uuid.UUID) -> tuple[Decimal, Decimal]:
        total_debits = Decimal("0.0000")
        total_credits = Decimal("0.0000")
        for p in self._postings:
            if p.account_id == account_id:
                if p.direction == PostingDirection.DEBIT:
                    total_debits += p.amount
                elif p.direction == PostingDirection.CREDIT:
                    total_credits += p.amount
        return total_debits, total_credits
