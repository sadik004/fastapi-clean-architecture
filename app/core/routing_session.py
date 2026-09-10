"""Database Dynamic Multi-Engine Routing & Read-Your-Own-Writes Protection.

Implements enterprise-grade Read/Write Replica Splitting:
1. Primary / Writer Engine: Routes INSERT, UPDATE, DELETE, and transactional mutations.
2. Read Replica Engine: Routes high-throughput SELECT queries, shedding load from Master.
3. Read-Your-Own-Writes Lag Guard: Once a write mutation is performed inside a transaction context,
   subsequent reads automatically stick to the Primary engine to prevent reading stale data.
4. Replica Mutation Shield: Forbids mutating operations against the read replica at driver level.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from enum import Enum
from types import TracebackType
from typing import Any, Self

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.database import (
    PrimaryAsyncSession,
    ReplicaAsyncSession,
    replica_engine,
)
from app.core.exceptions import ReadOnlyReplicaMutationException
from app.repositories.order_repository import (
    OrderRepositoryProtocol,
    SqlAlchemyOrderRepository,
)
from app.repositories.outbox_repository import (
    OutboxRepositoryProtocol,
    SqlAlchemyOutboxRepository,
)
from app.repositories.post_repository import (
    PostRepositoryProtocol,
    SqlAlchemyPostRepository,
)
from app.repositories.product_repository import (
    ProductRepositoryProtocol,
    SqlAlchemyProductRepository,
)
from app.repositories.sqlalchemy_user_repository import SqlAlchemyUserRepository
from app.repositories.user_repository import UserRepositoryProtocol

logger = logging.getLogger("app.core.routing_session")


class DatabaseRole(str, Enum):
    """Execution target role for multi-engine database operations."""

    PRIMARY = "PRIMARY"
    REPLICA = "REPLICA"


# =============================================================================
# Driver-Level Replica Mutation Shield
# =============================================================================


@event.listens_for(replica_engine.sync_engine, "before_cursor_execute")
def _guard_replica_mutations(
    conn: Any,
    cursor: Any,
    statement: str,
    parameters: Any,
    context: Any,
    executemany: bool,
) -> None:
    """Enforce zero mutations on the read replica engine at the cursor execution layer."""
    normalized = statement.strip().upper()
    for forbidden_prefix in ("INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "CREATE", "TRUNCATE"):
        if normalized.startswith(forbidden_prefix):
            logger.error(
                "Violation: Mutating SQL statement attempted on read replica: %s",
                statement[:60],
            )
            raise ReadOnlyReplicaMutationException(
                f"Mutating operation '{forbidden_prefix}' is strictly prohibited on read replica engine."
            )


# =============================================================================
# Routing Unit of Work
# =============================================================================


class RoutingUnitOfWork:
    """Asynchronous Unit of Work with Dynamic Read/Write Routing & Replication Lag Guard.

    Guarantees:
    - Writes and transactions route strictly to PrimaryAsyncSession.
    - Clean reads route to ReplicaAsyncSession.
    - Read-Your-Own-Writes Protection: If a mutation occurs in this context, subsequent
      reads stick to PrimaryAsyncSession to prevent reading stale replica data.
    """

    __slots__ = (
        "_primary_factory",
        "_replica_factory",
        "_primary_session",
        "_replica_session",
        "_has_written",
        "_users",
        "_posts",
        "_products",
        "_orders",
        "_outbox",
    )

    def __init__(
        self,
        primary_factory: async_sessionmaker[AsyncSession] | None = None,
        replica_factory: async_sessionmaker[AsyncSession] | None = None,
    ) -> None:
        self._primary_factory = primary_factory or PrimaryAsyncSession
        self._replica_factory = replica_factory or ReplicaAsyncSession
        self._primary_session: AsyncSession | None = None
        self._replica_session: AsyncSession | None = None
        self._has_written: bool = False
        self._users: SqlAlchemyUserRepository | None = None
        self._posts: SqlAlchemyPostRepository | None = None
        self._products: SqlAlchemyProductRepository | None = None
        self._orders: SqlAlchemyOrderRepository | None = None
        self._outbox: SqlAlchemyOutboxRepository | None = None

    @property
    def has_written(self) -> bool:
        """Whether a write mutation has been executed in this unit of work."""
        return self._has_written

    @property
    def current_read_target(self) -> DatabaseRole:
        """Target database engine for read queries (PRIMARY if written, else REPLICA)."""
        return DatabaseRole.PRIMARY if self._has_written else DatabaseRole.REPLICA

    def mark_written(self) -> None:
        """Explicitly flag that a mutating write occurred in this context."""
        self._has_written = True

    @property
    def primary_session(self) -> AsyncSession:
        """Active primary / writer session."""
        if self._primary_session is None:
            raise RuntimeError("RoutingUnitOfWork is not open. Access within 'async with uow:' block.")
        return self._primary_session

    @property
    def replica_session(self) -> AsyncSession:
        """Active read replica session."""
        if self._replica_session is None:
            raise RuntimeError("RoutingUnitOfWork is not open. Access within 'async with uow:' block.")
        return self._replica_session

    @property
    def active_read_session(self) -> AsyncSession:
        """Return replica session for clean reads, or stick to primary if written."""
        if self._has_written:
            return self.primary_session
        return self.replica_session

    @property
    def active_write_session(self) -> AsyncSession:
        """Return primary session and activate Read-Your-Own-Writes stickiness."""
        self._has_written = True
        return self.primary_session

    @property
    def session(self) -> AsyncSession:
        """Backwards-compatible alias for primary write session."""
        return self.active_write_session

    # Repository accessors (transactions / writes route to primary)
    @property
    def users(self) -> UserRepositoryProtocol:
        if self._users is None:
            raise RuntimeError("RoutingUnitOfWork is not open. Access within 'async with uow:' block.")
        self._has_written = True
        return self._users

    @property
    def posts(self) -> PostRepositoryProtocol:
        if self._posts is None:
            raise RuntimeError("RoutingUnitOfWork is not open. Access within 'async with uow:' block.")
        self._has_written = True
        return self._posts

    @property
    def products(self) -> ProductRepositoryProtocol:
        if self._products is None:
            raise RuntimeError("RoutingUnitOfWork is not open. Access within 'async with uow:' block.")
        self._has_written = True
        return self._products

    @property
    def orders(self) -> OrderRepositoryProtocol:
        if self._orders is None:
            raise RuntimeError("RoutingUnitOfWork is not open. Access within 'async with uow:' block.")
        self._has_written = True
        return self._orders

    @property
    def outbox(self) -> OutboxRepositoryProtocol:
        if self._outbox is None:
            raise RuntimeError("RoutingUnitOfWork is not open. Access within 'async with uow:' block.")
        self._has_written = True
        return self._outbox

    async def execute_read(self, statement: Any) -> Any:
        """Execute a SELECT query against the appropriate session with replication lag guard."""
        session = self.active_read_session
        return await session.execute(statement)

    async def execute_write(self, statement: Any) -> Any:
        """Execute an INSERT, UPDATE, or DELETE against the Primary master session."""
        session = self.active_write_session
        return await session.execute(statement)

    async def __aenter__(self) -> Self:
        self._primary_session = self._primary_factory()
        self._replica_session = self._replica_factory()
        self._users = SqlAlchemyUserRepository(session=self._primary_session)
        self._posts = SqlAlchemyPostRepository(session=self._primary_session)
        self._products = SqlAlchemyProductRepository(session=self._primary_session)
        self._orders = SqlAlchemyOrderRepository(session=self._primary_session)
        self._outbox = SqlAlchemyOutboxRepository(session=self._primary_session)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        try:
            if exc_type is not None:
                await self.rollback()
        finally:
            if self._primary_session is not None:
                await self._primary_session.close()
                self._primary_session = None
            if self._replica_session is not None:
                await self._replica_session.close()
                self._replica_session = None
            self._users = None
            self._posts = None
            self._products = None
            self._orders = None
            self._outbox = None

    async def commit(self) -> None:
        """Commit all pending write operations in the primary session."""
        if self._primary_session is None:
            raise RuntimeError("RoutingUnitOfWork is not open. Call within 'async with uow:' block.")
        await self._primary_session.commit()

    async def rollback(self) -> None:
        """Roll back all pending operations in the primary session."""
        if self._primary_session is None:
            raise RuntimeError("RoutingUnitOfWork is not open. Call within 'async with uow:' block.")
        await self._primary_session.rollback()


# =============================================================================
# FastAPI Dependency Providers
# =============================================================================


async def get_read_session() -> AsyncGenerator[AsyncSession]:
    """Yield an active read-only AsyncSession bound to the Replica engine."""
    session = ReplicaAsyncSession()
    try:
        yield session
    finally:
        await session.close()


async def get_primary_session() -> AsyncGenerator[AsyncSession]:
    """Yield an active transactional AsyncSession bound to the Primary engine."""
    session = PrimaryAsyncSession()
    try:
        yield session
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()


def get_primary_uow() -> RoutingUnitOfWork:
    """Dependency provider yielding a RoutingUnitOfWork instance."""
    return RoutingUnitOfWork()


async def get_routing_uow() -> AsyncGenerator[RoutingUnitOfWork]:
    """Dependency provider yielding a context-managed RoutingUnitOfWork instance."""
    async with RoutingUnitOfWork() as uow:
        yield uow
