"""SQLAlchemy 2.0 Declarative ORM Entity for Orders with Time-Ordered UUIDv7 Primary Key."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import DateTime, Float, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import CHAR, TypeDecorator

from app.core.database import Base
from app.core.identifiers import generate_uuidv7

if TYPE_CHECKING:
    from app.models.user import UserModel


class GUID(TypeDecorator[uuid.UUID]):
    """Platform-independent GUID/UUID type.

    Uses PostgreSQL's native UUID type when running against Postgres,
    otherwise uses CHAR(36) for SQLite and cross-platform compatibility.
    """

    impl = CHAR
    cache_ok = True

    def load_dialect_impl(self, dialect: Any) -> Any:
        if dialect.name == "postgresql":
            return dialect.type_descriptor(PG_UUID(as_uuid=True))
        else:
            return dialect.type_descriptor(CHAR(36))

    def process_bind_param(self, value: uuid.UUID | str | None, dialect: Any) -> str | uuid.UUID | None:
        if value is None:
            return None
        elif dialect.name == "postgresql":
            return value if isinstance(value, uuid.UUID) else uuid.UUID(value)
        else:
            return str(value)

    def process_result_value(self, value: str | uuid.UUID | None, dialect: Any) -> uuid.UUID | None:
        if value is None:
            return None
        if isinstance(value, uuid.UUID):
            return value
        return uuid.UUID(str(value))


class OrderModel(Base):
    """Order database entity utilizing RFC 9562 UUIDv7 as the clustered primary key.

    Guarantees:
    1. B-Tree Clustered Locality: Insertions are monotonically right-appended to leaf nodes.
    2. Zero Auto-Increment Leaks: Order counts and business volumes are not exposed.
    3. Creation Timestamp Embedded: Top 48 bits store millisecond timestamp.
    """

    __tablename__ = "orders"

    id: Mapped[uuid.UUID] = mapped_column(
        GUID(),
        primary_key=True,
        default=generate_uuidv7,
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    total_amount: Mapped[float] = mapped_column(Float, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="pending", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=func.now(),
        server_default=func.now(),
        nullable=False,
    )

    # Many-to-One relationship to User with lazy="raise" boundary enforcement
    customer: Mapped[UserModel] = relationship(
        "UserModel",
        lazy="raise",
    )
