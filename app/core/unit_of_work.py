"""Unit of Work (UoW) Pattern implementation for atomic ACID transactions across repositories."""

import uuid
from types import TracebackType
from typing import Protocol, Self

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.database import async_session_factory
from app.repositories.ledger_repository import (
    InMemoryLedgerRepository,
    JournalEntryEntity,
    JournalPostingEntity,
    LedgerAccountEntity,
    LedgerRepositoryProtocol,
    SqlAlchemyLedgerRepository,
)
from app.repositories.order_repository import (
    InMemoryOrderRepository,
    OrderEntity,
    OrderRepositoryProtocol,
    SqlAlchemyOrderRepository,
)
from app.repositories.outbox_repository import (
    InMemoryOutboxRepository,
    OutboxEventEntity,
    OutboxRepositoryProtocol,
    SqlAlchemyOutboxRepository,
)
from app.repositories.post_repository import (
    InMemoryPostRepository,
    PostEntity,
    PostRepositoryProtocol,
    SqlAlchemyPostRepository,
)
from app.repositories.product_repository import (
    InMemoryProductRepository,
    ProductEntity,
    ProductRepositoryProtocol,
    SqlAlchemyProductRepository,
)
from app.repositories.sqlalchemy_user_repository import SqlAlchemyUserRepository
from app.repositories.user_repository import (
    InMemoryUserRepository,
    UserEntity,
    UserRepositoryProtocol,
)


class UnitOfWorkProtocol(Protocol):
    """Abstract protocol defining the Unit of Work interface."""

    @property
    def users(self) -> UserRepositoryProtocol:
        """User repository operating on the shared transaction."""
        ...

    @property
    def posts(self) -> PostRepositoryProtocol:
        """Post repository operating on the shared transaction."""
        ...

    @property
    def products(self) -> ProductRepositoryProtocol:
        """Product repository operating on the shared transaction."""
        ...

    @property
    def orders(self) -> OrderRepositoryProtocol:
        """Order repository operating on the shared transaction."""
        ...

    @property
    def outbox(self) -> OutboxRepositoryProtocol:
        """Transactional outbox repository operating on the shared transaction."""
        ...

    @property
    def ledger(self) -> LedgerRepositoryProtocol:
        """Ledger repository operating on the shared transaction."""
        ...

    async def __aenter__(self) -> Self:
        """Open the transaction boundary and initialize repositories with the shared session."""
        ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        """Exit the transaction boundary, auto-rolling back on error and closing the session."""
        ...

    async def commit(self) -> None:
        """Explicitly commit all staged operations across all registered repositories."""
        ...

    async def rollback(self) -> None:
        """Explicitly roll back all staged operations across all registered repositories."""
        ...


class SqlAlchemyUnitOfWork:
    """Production asynchronous Unit of Work backed by SQLAlchemy 2.0.

    Guarantees:
    - Atomicity: All repository operations execute on the exact same underlying AsyncSession.
    - Automatic Rollback: Any unhandled exception during the context block triggers await self.rollback().
    - Zero Resource Leaks: The session is unconditionally closed in a finally block upon exit.
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession] | None = None,
    ) -> None:
        self._session_factory: async_sessionmaker[AsyncSession] = session_factory or async_session_factory
        self.session: AsyncSession | None = None
        self._users: SqlAlchemyUserRepository | None = None
        self._posts: SqlAlchemyPostRepository | None = None
        self._products: SqlAlchemyProductRepository | None = None
        self._orders: SqlAlchemyOrderRepository | None = None
        self._outbox: SqlAlchemyOutboxRepository | None = None
        self._ledger: SqlAlchemyLedgerRepository | None = None

    @property
    def users(self) -> UserRepositoryProtocol:
        if self._users is None:
            raise RuntimeError("UnitOfWork is not open. Access repositories within 'async with uow:' block.")
        return self._users

    @property
    def posts(self) -> PostRepositoryProtocol:
        if self._posts is None:
            raise RuntimeError("UnitOfWork is not open. Access repositories within 'async with uow:' block.")
        return self._posts

    @property
    def products(self) -> ProductRepositoryProtocol:
        if self._products is None:
            raise RuntimeError("UnitOfWork is not open. Access repositories within 'async with uow:' block.")
        return self._products

    @property
    def orders(self) -> OrderRepositoryProtocol:
        if self._orders is None:
            raise RuntimeError("UnitOfWork is not open. Access repositories within 'async with uow:' block.")
        return self._orders

    @property
    def outbox(self) -> OutboxRepositoryProtocol:
        if self._outbox is None:
            raise RuntimeError("UnitOfWork is not open. Access repositories within 'async with uow:' block.")
        return self._outbox

    @property
    def ledger(self) -> LedgerRepositoryProtocol:
        if self._ledger is None:
            raise RuntimeError("UnitOfWork is not open. Access repositories within 'async with uow:' block.")
        return self._ledger

    async def __aenter__(self) -> Self:
        self.session = self._session_factory()
        self._users = SqlAlchemyUserRepository(session=self.session)
        self._posts = SqlAlchemyPostRepository(session=self.session)
        self._products = SqlAlchemyProductRepository(session=self.session)
        self._orders = SqlAlchemyOrderRepository(session=self.session)
        self._outbox = SqlAlchemyOutboxRepository(session=self.session)
        self._ledger = SqlAlchemyLedgerRepository(session=self.session)
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
            if self.session is not None:
                await self.session.close()
                self.session = None
                self._users = None
                self._posts = None
                self._products = None
                self._orders = None
                self._outbox = None
                self._ledger = None

    async def commit(self) -> None:
        """Commit all pending operations in the active session."""
        if self.session is None:
            raise RuntimeError("UnitOfWork is not open. Call within 'async with uow:' block.")
        await self.session.commit()

    async def rollback(self) -> None:
        """Roll back all pending operations in the active session."""
        if self.session is None:
            raise RuntimeError("UnitOfWork is not open. Call within 'async with uow:' block.")
        await self.session.rollback()


class InMemoryUnitOfWork:
    """In-memory implementation of UnitOfWorkProtocol with snapshot-based rollback simulation."""

    def __init__(
        self,
        user_repo: InMemoryUserRepository | None = None,
        post_repo: InMemoryPostRepository | None = None,
        product_repo: InMemoryProductRepository | None = None,
        order_repo: InMemoryOrderRepository | None = None,
        outbox_repo: InMemoryOutboxRepository | None = None,
        ledger_repo: InMemoryLedgerRepository | None = None,
    ) -> None:
        self.users: InMemoryUserRepository = user_repo or InMemoryUserRepository()
        self.posts: InMemoryPostRepository = post_repo or InMemoryPostRepository()
        self.products: InMemoryProductRepository = product_repo or InMemoryProductRepository()
        self.orders: InMemoryOrderRepository = order_repo or InMemoryOrderRepository()
        self.outbox: InMemoryOutboxRepository = outbox_repo or InMemoryOutboxRepository()
        self.ledger: InMemoryLedgerRepository = ledger_repo or InMemoryLedgerRepository()

        # State snapshots for rollback restoration
        self._user_store_snapshot: dict[int, UserEntity] | None = None
        self._user_email_snapshot: dict[str, int] | None = None
        self._user_username_snapshot: dict[str, int] | None = None
        self._user_id_snapshot: int | None = None

        self._post_store_snapshot: dict[int, PostEntity] | None = None
        self._post_id_snapshot: int | None = None

        self._product_store_snapshot: dict[int, ProductEntity] | None = None
        self._product_id_snapshot: int | None = None

        self._order_store_snapshot: dict[uuid.UUID, OrderEntity] | None = None
        self._outbox_store_snapshot: dict[uuid.UUID, OutboxEventEntity] | None = None

        self._ledger_accounts_snapshot: dict[uuid.UUID, LedgerAccountEntity] | None = None
        self._ledger_accounts_by_num_snapshot: dict[str, uuid.UUID] | None = None
        self._ledger_entries_snapshot: dict[uuid.UUID, JournalEntryEntity] | None = None
        self._ledger_entries_by_ref_snapshot: dict[str, uuid.UUID] | None = None
        self._ledger_postings_snapshot: list[JournalPostingEntity] | None = None

    async def __aenter__(self) -> Self:
        # Snapshot in-memory repositories state
        self._user_store_snapshot = dict(self.users._store)
        self._user_email_snapshot = dict(self.users._email_index)
        self._user_username_snapshot = dict(self.users._username_index)
        self._user_id_snapshot = self.users._current_id

        self._post_store_snapshot = dict(self.posts._store)
        self._post_id_snapshot = self.posts._current_id

        self._product_store_snapshot = dict(self.products._store)
        self._product_id_snapshot = self.products._current_id

        self._order_store_snapshot = dict(self.orders._store)
        self._outbox_store_snapshot = dict(self.outbox._store)
        self._ledger_accounts_snapshot = dict(self.ledger._accounts)
        self._ledger_accounts_by_num_snapshot = dict(self.ledger._accounts_by_number)
        self._ledger_entries_snapshot = dict(self.ledger._entries)
        self._ledger_entries_by_ref_snapshot = dict(self.ledger._entries_by_ref)
        self._ledger_postings_snapshot = list(self.ledger._postings)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        if exc_type is not None:
            await self.rollback()
        self._clear_snapshots()

    def _clear_snapshots(self) -> None:
        self._user_store_snapshot = None
        self._user_email_snapshot = None
        self._user_username_snapshot = None
        self._user_id_snapshot = None
        self._post_store_snapshot = None
        self._post_id_snapshot = None
        self._product_store_snapshot = None
        self._product_id_snapshot = None
        self._order_store_snapshot = None
        self._outbox_store_snapshot = None
        self._ledger_accounts_snapshot = None
        self._ledger_accounts_by_num_snapshot = None
        self._ledger_entries_snapshot = None
        self._ledger_entries_by_ref_snapshot = None
        self._ledger_postings_snapshot = None

    async def commit(self) -> None:
        """Commit in-memory changes by discarding rollback snapshots."""
        self._clear_snapshots()

    async def rollback(self) -> None:
        """Roll back in-memory changes by restoring from entry snapshots."""
        if self._user_store_snapshot is not None:
            self.users._store = dict(self._user_store_snapshot)
        if self._user_email_snapshot is not None:
            self.users._email_index = dict(self._user_email_snapshot)
        if self._user_username_snapshot is not None:
            self.users._username_index = dict(self._user_username_snapshot)
        if self._user_id_snapshot is not None:
            self.users._current_id = self._user_id_snapshot

        if self._post_store_snapshot is not None:
            self.posts._store = dict(self._post_store_snapshot)
        if self._post_id_snapshot is not None:
            self.posts._current_id = self._post_id_snapshot

        if self._product_store_snapshot is not None:
            self.products._store = dict(self._product_store_snapshot)
        if self._product_id_snapshot is not None:
            self.products._current_id = self._product_id_snapshot

        if self._order_store_snapshot is not None:
            self.orders._store = dict(self._order_store_snapshot)

        if self._outbox_store_snapshot is not None:
            self.outbox._store = dict(self._outbox_store_snapshot)

        if self._ledger_accounts_snapshot is not None:
            self.ledger._accounts = dict(self._ledger_accounts_snapshot)
        if self._ledger_accounts_by_num_snapshot is not None:
            self.ledger._accounts_by_number = dict(self._ledger_accounts_by_num_snapshot)
        if self._ledger_entries_snapshot is not None:
            self.ledger._entries = dict(self._ledger_entries_snapshot)
        if self._ledger_entries_by_ref_snapshot is not None:
            self.ledger._entries_by_ref = dict(self._ledger_entries_by_ref_snapshot)
        if self._ledger_postings_snapshot is not None:
            self.ledger._postings = list(self._ledger_postings_snapshot)


__all__ = [
    "InMemoryUnitOfWork",
    "SqlAlchemyUnitOfWork",
    "UnitOfWorkProtocol",
]
