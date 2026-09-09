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
    - Atomicity: Co-located with entity mutations (orders, users, payments).
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
