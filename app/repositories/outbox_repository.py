"""Transactional Outbox Repository implementations and Protocol."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.identifiers import generate_uuidv7
from app.models.outbox import OutboxEventModel


@dataclass(slots=True)
class OutboxEventEntity:
    """In-memory domain entity representation of an Outbox record."""

    id: uuid.UUID
    event_type: str
    aggregate_type: str
    aggregate_id: str
    payload: dict[str, Any]
    status: str
    retry_count: int
    created_at: datetime
    published_at: datetime | None = None

    @property
    def topic(self) -> str:
        """Alias for aggregate_type representing the message broker topic."""
        return self.aggregate_type

    @property
    def partition_key(self) -> str:
        """Alias for aggregate_id representing Kafka message partition key."""
        return self.aggregate_id

    @property
    def processed_at(self) -> datetime | None:
        """Alias for published_at representing when event relay processed this record."""
        return self.published_at


class OutboxRepositoryProtocol(Protocol):
    """Abstract protocol for Transactional Outbox persistence operations."""

    async def create(self, event: OutboxEventModel | OutboxEventEntity) -> OutboxEventEntity:
        """Persist an outbox event model or entity within the active transaction."""
        ...

    async def record_event(
        self,
        event_type: str,
        aggregate_type: str,
        aggregate_id: str,
        payload: dict[str, Any],
        event_id: uuid.UUID | None = None,
    ) -> OutboxEventEntity:
        """Record an outbound event within the active transaction boundary."""
        ...

    async def get_pending_events(self, batch_size: int = 50) -> list[OutboxEventEntity]:
        """Fetch un-published outbox events ordered monotonically by created_at."""
        ...

    async def mark_published(self, event_id: uuid.UUID, status: str = "PUBLISHED") -> OutboxEventEntity | None:
        """Update event status to PUBLISHED or PROCESSED with a UTC timestamp."""
        ...

    async def mark_failed(self, event_id: uuid.UUID, max_retries: int = 3) -> OutboxEventEntity | None:
        """Increment retry count, transitioning to FAILED if max retries exceeded."""
        ...

    async def get_by_id(self, event_id: uuid.UUID) -> OutboxEventEntity | None:
        """Fetch an outbox record by its UUIDv7 primary key."""
        ...

    async def list_recent_events(self, limit: int = 50) -> list[OutboxEventEntity]:
        """List recently recorded outbox events for monitoring and observability."""
        ...


class SqlAlchemyOutboxRepository:
    """Production SQLAlchemy 2.0 implementation of OutboxRepositoryProtocol."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def _to_entity(self, model: OutboxEventModel) -> OutboxEventEntity:
        return OutboxEventEntity(
            id=model.id,
            event_type=model.event_type,
            aggregate_type=model.aggregate_type,
            aggregate_id=model.aggregate_id,
            payload=dict(model.payload),
            status=model.status,
            retry_count=model.retry_count,
            created_at=model.created_at,
            published_at=model.published_at,
        )

    async def create(self, event: OutboxEventModel | OutboxEventEntity) -> OutboxEventEntity:
        if isinstance(event, OutboxEventModel):
            model = event
        else:
            model = OutboxEventModel(
                id=event.id,
                event_type=event.event_type,
                aggregate_type=event.aggregate_type,
                aggregate_id=event.aggregate_id,
                payload=event.payload,
                status=event.status,
                retry_count=event.retry_count,
                created_at=event.created_at,
                published_at=event.published_at,
            )
        self._session.add(model)
        await self._session.flush()
        return self._to_entity(model)

    async def record_event(
        self,
        event_type: str,
        aggregate_type: str,
        aggregate_id: str,
        payload: dict[str, Any],
        event_id: uuid.UUID | None = None,
    ) -> OutboxEventEntity:
        model = OutboxEventModel(
            id=event_id or generate_uuidv7(),
            event_type=event_type,
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            payload=payload,
            status="PENDING",
            retry_count=0,
            created_at=datetime.now(UTC),
            published_at=None,
        )
        self._session.add(model)
        await self._session.flush()
        return self._to_entity(model)

    async def get_pending_events(self, batch_size: int = 50) -> list[OutboxEventEntity]:
        stmt = (
            select(OutboxEventModel)
            .where(OutboxEventModel.status == "PENDING")
            .order_by(OutboxEventModel.created_at.asc())
            .limit(batch_size)
        )
        result = await self._session.execute(stmt)
        models = result.scalars().all()
        return [self._to_entity(m) for m in models]

    async def mark_published(self, event_id: uuid.UUID, status: str = "PUBLISHED") -> OutboxEventEntity | None:
        model = await self._session.get(OutboxEventModel, event_id)
        if model is None:
            return None
        model.status = status
        model.published_at = datetime.now(UTC)
        await self._session.flush()
        return self._to_entity(model)

    async def mark_failed(self, event_id: uuid.UUID, max_retries: int = 3) -> OutboxEventEntity | None:
        model = await self._session.get(OutboxEventModel, event_id)
        if model is None:
            return None
        model.retry_count += 1
        if model.retry_count >= max_retries:
            model.status = "FAILED"
        await self._session.flush()
        return self._to_entity(model)

    async def get_by_id(self, event_id: uuid.UUID) -> OutboxEventEntity | None:
        model = await self._session.get(OutboxEventModel, event_id)
        if model is None:
            return None
        return self._to_entity(model)

    async def list_recent_events(self, limit: int = 50) -> list[OutboxEventEntity]:
        stmt = select(OutboxEventModel).order_by(desc(OutboxEventModel.created_at)).limit(limit)
        result = await self._session.execute(stmt)
        models = result.scalars().all()
        return [self._to_entity(m) for m in models]


class InMemoryOutboxRepository:
    """In-memory implementation of OutboxRepositoryProtocol for fast unit testing."""

    def __init__(self) -> None:
        self._store: dict[uuid.UUID, OutboxEventEntity] = {}

    async def create(self, event: OutboxEventModel | OutboxEventEntity) -> OutboxEventEntity:
        if isinstance(event, OutboxEventModel):
            entity = OutboxEventEntity(
                id=event.id or generate_uuidv7(),
                event_type=event.event_type,
                aggregate_type=event.aggregate_type,
                aggregate_id=event.aggregate_id,
                payload=dict(event.payload),
                status=event.status or "PENDING",
                retry_count=event.retry_count or 0,
                created_at=event.created_at or datetime.now(UTC),
                published_at=event.published_at,
            )
        else:
            entity = event
        self._store[entity.id] = entity
        return entity

    async def record_event(
        self,
        event_type: str,
        aggregate_type: str,
        aggregate_id: str,
        payload: dict[str, Any],
        event_id: uuid.UUID | None = None,
    ) -> OutboxEventEntity:
        eid = event_id or generate_uuidv7()
        entity = OutboxEventEntity(
            id=eid,
            event_type=event_type,
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            payload=dict(payload),
            status="PENDING",
            retry_count=0,
            created_at=datetime.now(UTC),
            published_at=None,
        )
        self._store[eid] = entity
        return entity

    async def get_pending_events(self, batch_size: int = 50) -> list[OutboxEventEntity]:
        pending = [e for e in self._store.values() if e.status == "PENDING"]
        pending.sort(key=lambda x: x.created_at)
        return pending[:batch_size]

    async def mark_published(self, event_id: uuid.UUID, status: str = "PUBLISHED") -> OutboxEventEntity | None:
        entity = self._store.get(event_id)
        if entity is None:
            return None
        updated = OutboxEventEntity(
            id=entity.id,
            event_type=entity.event_type,
            aggregate_type=entity.aggregate_type,
            aggregate_id=entity.aggregate_id,
            payload=entity.payload,
            status=status,
            retry_count=entity.retry_count,
            created_at=entity.created_at,
            published_at=datetime.now(UTC),
        )
        self._store[event_id] = updated
        return updated

    async def mark_failed(self, event_id: uuid.UUID, max_retries: int = 3) -> OutboxEventEntity | None:
        entity = self._store.get(event_id)
        if entity is None:
            return None
        new_retries = entity.retry_count + 1
        new_status = "FAILED" if new_retries >= max_retries else "PENDING"
        updated = OutboxEventEntity(
            id=entity.id,
            event_type=entity.event_type,
            aggregate_type=entity.aggregate_type,
            aggregate_id=entity.aggregate_id,
            payload=entity.payload,
            status=new_status,
            retry_count=new_retries,
            created_at=entity.created_at,
            published_at=entity.published_at,
        )
        self._store[event_id] = updated
        return updated

    async def get_by_id(self, event_id: uuid.UUID) -> OutboxEventEntity | None:
        return self._store.get(event_id)

    async def list_recent_events(self, limit: int = 50) -> list[OutboxEventEntity]:
        events = list(self._store.values())
        events.sort(key=lambda x: x.created_at, reverse=True)
        return events[:limit]
