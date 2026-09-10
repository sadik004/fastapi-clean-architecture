"""SQLAlchemy 2.0 Declarative ORM Model for Partitioned Audit Logs.

Implements PostgreSQL Declarative Range Partitioning by `RANGE (created_at)`.
Enforces the mandatory PostgreSQL Partitioning Composite Primary Key Invariant:
The partition key column (`created_at`) MUST be included in the primary key.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.core.identifiers import generate_uuidv7
from app.models.order import GUID


class AuditLogPartitionModel(Base):
    """Enterprise Audit Log entity partitioned by Range on `created_at`.

    Architectural Invariants:
    1. Declarative Range Partitioning: Partitioned by `RANGE (created_at)`.
    2. Composite Primary Key Invariant: PostgreSQL mandates that all unique constraints
       and primary keys on a partitioned table must include the partition key (`created_at`).
       Therefore, the primary key is composite: `(id, created_at)`.
    3. Monotonic UUIDv7: Generates time-ordered UUIDv7 identifiers ensuring B-Tree index locality.
    """

    __tablename__ = "audit_logs"
    __table_args__ = (
        {"postgresql_partition_by": "RANGE (created_at)"},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        GUID(),
        primary_key=True,
        default=generate_uuidv7,
        doc="RFC 9562 UUIDv7 unique identifier for the audit log record.",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        primary_key=True,
        default=lambda: datetime.now(UTC),
        doc="Partition key timestamp stored in UTC. Mandatory part of composite primary key.",
    )
    event_type: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        index=True,
        doc="High-cardinality category/type of the audit event (e.g. USER_LOGIN, ORDER_PLACED).",
    )
    user_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        index=True,
        doc="ID of the actor/user associated with the audit event.",
    )
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSON,
        nullable=False,
        default=dict,
        doc="Structured event metadata and audit payload.",
    )

    def __repr__(self) -> str:
        return (
            f"<AuditLogPartitionModel(id={self.id}, created_at={self.created_at}, "
            f"event_type='{self.event_type}', user_id={self.user_id})>"
        )
