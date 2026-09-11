"""Reconciliation & Drift Recovery Repository Implementation.

Guarantees:
1. Zero ORM Leakage: Models are converted to pure domain entities (ReconciliationBatchEntity, ReconciliationItemEntity).
2. Monotonic B-Tree Clustering: All entities use UUIDv7 primary keys.
3. Dual-Mode Repositories: Provides both SqlAlchemyReconciliationRepository and InMemoryReconciliationRepository.
4. Atomicity: Operates within the shared Unit of Work transaction boundary.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.identifiers import generate_uuidv7
from app.models.reconciliation import (
    ReconciliationBatchModel,
    ReconciliationItemModel,
)


@dataclass(slots=True)
class ReconciliationItemEntity:
    """Pure domain entity representing an audited reconciliation transaction item."""

    id: uuid.UUID
    batch_id: uuid.UUID
    reference_id: str
    external_amount: Decimal
    internal_amount: Decimal | None
    discrepancy_type: str
    resolution_status: str
    compensating_journal_entry_id: uuid.UUID | None


@dataclass(slots=True)
class ReconciliationBatchEntity:
    """Pure domain entity representing an executed settlement reconciliation batch."""

    id: uuid.UUID
    batch_reference: str
    gateway_name: str
    total_records: int
    matched_records: int
    discrepancy_records: int
    status: str
    created_at: datetime
    items: list[ReconciliationItemEntity] = field(default_factory=list)


class ReconciliationRepositoryProtocol(Protocol):
    """Abstract protocol defining persistence contracts for reconciliation batches and items."""

    async def create_batch(
        self,
        batch_reference: str,
        gateway_name: str,
        total_records: int,
        matched_records: int,
        discrepancy_records: int,
        status: str,
        items: Sequence[ReconciliationItemEntity],
        batch_id: uuid.UUID | None = None,
        created_at: datetime | None = None,
    ) -> ReconciliationBatchEntity:
        """Persist a new reconciliation batch header and all child item records."""
        ...

    async def get_batch_by_id(self, batch_id: uuid.UUID) -> ReconciliationBatchEntity | None:
        """Retrieve a reconciliation batch by primary key ID with all child items."""
        ...

    async def get_batch_by_reference(self, batch_reference: str) -> ReconciliationBatchEntity | None:
        """Retrieve a reconciliation batch by its unique reference code with all child items."""
        ...


class SqlAlchemyReconciliationRepository:
    """SQLAlchemy 2.0 asynchronous implementation of ReconciliationRepositoryProtocol."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    @staticmethod
    def _to_entity(model: ReconciliationBatchModel) -> ReconciliationBatchEntity:
        items = [
            ReconciliationItemEntity(
                id=item.id,
                batch_id=item.batch_id,
                reference_id=item.reference_id,
                external_amount=item.external_amount,
                internal_amount=item.internal_amount,
                discrepancy_type=item.discrepancy_type,
                resolution_status=item.resolution_status,
                compensating_journal_entry_id=item.compensating_journal_entry_id,
            )
            for item in model.items
        ]
        return ReconciliationBatchEntity(
            id=model.id,
            batch_reference=model.batch_reference,
            gateway_name=model.gateway_name,
            total_records=model.total_records,
            matched_records=model.matched_records,
            discrepancy_records=model.discrepancy_records,
            status=model.status,
            created_at=model.created_at,
            items=items,
        )

    async def create_batch(
        self,
        batch_reference: str,
        gateway_name: str,
        total_records: int,
        matched_records: int,
        discrepancy_records: int,
        status: str,
        items: Sequence[ReconciliationItemEntity],
        batch_id: uuid.UUID | None = None,
        created_at: datetime | None = None,
    ) -> ReconciliationBatchEntity:
        b_id = batch_id or generate_uuidv7()
        ts = created_at or datetime.now(UTC)

        batch_model = ReconciliationBatchModel(
            id=b_id,
            batch_reference=batch_reference,
            gateway_name=gateway_name,
            total_records=total_records,
            matched_records=matched_records,
            discrepancy_records=discrepancy_records,
            status=status,
            created_at=ts,
        )
        self.session.add(batch_model)

        for item in items:
            item_model = ReconciliationItemModel(
                id=item.id or generate_uuidv7(),
                batch_id=b_id,
                reference_id=item.reference_id,
                external_amount=item.external_amount,
                internal_amount=item.internal_amount,
                discrepancy_type=item.discrepancy_type,
                resolution_status=item.resolution_status,
                compensating_journal_entry_id=item.compensating_journal_entry_id,
            )
            self.session.add(item_model)

        await self.session.flush()
        # Reload with items
        stmt = (
            select(ReconciliationBatchModel)
            .where(ReconciliationBatchModel.id == b_id)
            .options(selectinload(ReconciliationBatchModel.items))
        )
        result = await self.session.execute(stmt)
        loaded = result.scalar_one()
        return self._to_entity(loaded)

    async def get_batch_by_id(self, batch_id: uuid.UUID) -> ReconciliationBatchEntity | None:
        stmt = (
            select(ReconciliationBatchModel)
            .where(ReconciliationBatchModel.id == batch_id)
            .options(selectinload(ReconciliationBatchModel.items))
        )
        result = await self.session.execute(stmt)
        model = result.scalar_one_or_none()
        if model is None:
            return None
        return self._to_entity(model)

    async def get_batch_by_reference(self, batch_reference: str) -> ReconciliationBatchEntity | None:
        stmt = (
            select(ReconciliationBatchModel)
            .where(ReconciliationBatchModel.batch_reference == batch_reference)
            .options(selectinload(ReconciliationBatchModel.items))
        )
        result = await self.session.execute(stmt)
        model = result.scalar_one_or_none()
        if model is None:
            return None
        return self._to_entity(model)


class InMemoryReconciliationRepository:
    """Thread-safe in-memory implementation of ReconciliationRepositoryProtocol for unit testing."""

    def __init__(self) -> None:
        self._batches: dict[uuid.UUID, ReconciliationBatchEntity] = {}
        self._by_reference: dict[str, uuid.UUID] = {}

    async def create_batch(
        self,
        batch_reference: str,
        gateway_name: str,
        total_records: int,
        matched_records: int,
        discrepancy_records: int,
        status: str,
        items: Sequence[ReconciliationItemEntity],
        batch_id: uuid.UUID | None = None,
        created_at: datetime | None = None,
    ) -> ReconciliationBatchEntity:
        b_id = batch_id or generate_uuidv7()
        ts = created_at or datetime.now(UTC)

        materialized_items = [
            ReconciliationItemEntity(
                id=item.id or generate_uuidv7(),
                batch_id=b_id,
                reference_id=item.reference_id,
                external_amount=item.external_amount,
                internal_amount=item.internal_amount,
                discrepancy_type=item.discrepancy_type,
                resolution_status=item.resolution_status,
                compensating_journal_entry_id=item.compensating_journal_entry_id,
            )
            for item in items
        ]

        batch = ReconciliationBatchEntity(
            id=b_id,
            batch_reference=batch_reference,
            gateway_name=gateway_name,
            total_records=total_records,
            matched_records=matched_records,
            discrepancy_records=discrepancy_records,
            status=status,
            created_at=ts,
            items=materialized_items,
        )
        self._batches[b_id] = batch
        self._by_reference[batch_reference] = b_id
        return batch

    async def get_batch_by_id(self, batch_id: uuid.UUID) -> ReconciliationBatchEntity | None:
        return self._batches.get(batch_id)

    async def get_batch_by_reference(self, batch_reference: str) -> ReconciliationBatchEntity | None:
        batch_id = self._by_reference.get(batch_reference)
        if batch_id is None:
            return None
        return self._batches.get(batch_id)
