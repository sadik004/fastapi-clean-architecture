"""SQLAlchemy 2.0 Declarative ORM Model for Transactional Outbox Pattern."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.core.identifiers import generate_uuidv7
from app.models.order import GUID


class OutboxEventModel(Base):
    """Transactional Outbox event record stored in the same ACID database transaction as domain entities.

    Guarantees:
    - Atomicity: Co-located with entity mutations (orders, users, payments, ledger).
    - Eventual Consistency: Dispatched asynchronously to message brokers by the Outbox Relay worker.
    - Zero Data Loss: Overcomes the distributed dual-write problem across databases and event brokers.
    """

    __tablename__ = "outbox_events"

    id: Mapped[uuid.UUID] = mapped_column(
        GUID,
        primary_key=True,
        default=generate_uuidv7,
    )
    event_type: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    aggregate_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    aggregate_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="PENDING", nullable=False, index=True)
    retry_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
        index=True,
    )
    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    def __init__(
        self,
        *,
        id: uuid.UUID | None = None,
        event_type: str,
        topic: str | None = None,
        aggregate_type: str | None = None,
        partition_key: str | None = None,
        aggregate_id: str | None = None,
        payload: dict[str, Any],
        status: str = "PENDING",
        retry_count: int = 0,
        created_at: datetime | None = None,
        published_at: datetime | None = None,
        **kwargs: Any,
    ) -> None:
        agg_type = topic or aggregate_type or "general"
        agg_id = partition_key or aggregate_id or "default"
        super().__init__(
            id=id or generate_uuidv7(),
            event_type=event_type,
            aggregate_type=agg_type,
            aggregate_id=agg_id,
            payload=payload,
            status=status,
            retry_count=retry_count,
            created_at=created_at or datetime.now(UTC),
            published_at=published_at,
            **kwargs,
        )

    @property
    def topic(self) -> str:
        """Alias for aggregate_type to cleanly support messaging topics."""
        return self.aggregate_type

    @topic.setter
    def topic(self, value: str) -> None:
        self.aggregate_type = value

    @property
    def partition_key(self) -> str:
        """Alias for aggregate_id representing Kafka message partition key."""
        return self.aggregate_id

    @partition_key.setter
    def partition_key(self, value: str) -> None:
        self.aggregate_id = value

    @property
    def processed_at(self) -> datetime | None:
        """Alias for published_at representing when event relay processed this record."""
        return self.published_at

    @processed_at.setter
    def processed_at(self, value: datetime | None) -> None:
        self.published_at = value
