"""SQLAlchemy 2.0 Asynchronous Database Configuration, Connection Pooling & Session Management."""

from collections.abc import AsyncGenerator
from typing import Any

from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncAttrs,
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.core.config import get_settings

settings = get_settings()

# Engine-level connection arguments & pool configuration helper
def _build_engine_and_pool_kwargs(
    url: str, is_replica: bool = False
) -> tuple[dict[str, Any], dict[str, Any]]:
    connect_args: dict[str, Any] = {}
    if url.startswith("postgresql"):
        # Configure PostgreSQL server-side connection execution options:
        # 1. statement_timeout: Hard kill runaway queries taking > db_statement_timeout_ms (3000ms)
        # 2. idle_in_transaction_session_timeout: Hard kill sessions holding open transactions idle > 5000ms
        # 3. lock_timeout: Rejects lock acquisitions waiting > 2000ms to prevent deadlocks
        connect_args["server_settings"] = {
            "statement_timeout": str(settings.db_statement_timeout_ms),
            "idle_in_transaction_session_timeout": str(settings.db_idle_in_transaction_timeout_ms),
            "lock_timeout": str(settings.db_lock_timeout_ms),
        }
    elif url.startswith("sqlite"):
        connect_args["check_same_thread"] = False
        connect_args["timeout"] = 5.0

    pool_kwargs: dict[str, Any] = {
        "pool_pre_ping": settings.db_pool_pre_ping,
        "pool_recycle": settings.db_pool_recycle,
    }
    is_sqlite_memory = ":memory:" in url or "mode=memory" in url
    if not is_sqlite_memory:
        pool_kwargs["pool_size"] = (
            settings.db_replica_pool_size if is_replica else settings.db_pool_size
        )
        pool_kwargs["max_overflow"] = (
            settings.db_replica_max_overflow if is_replica else settings.db_max_overflow
        )
        pool_kwargs["pool_timeout"] = settings.db_pool_timeout
    return connect_args, pool_kwargs


# Primary / Writer Asynchronous Engine
primary_connect_args, primary_pool_kwargs = _build_engine_and_pool_kwargs(
    settings.database_url, is_replica=False
)
primary_engine: AsyncEngine = create_async_engine(
    settings.database_url,
    echo=settings.db_echo,
    connect_args=primary_connect_args,
    **primary_pool_kwargs,
)

# Read Replica Asynchronous Engine (defaults to Primary if unset)
replica_url = settings.database_read_replica_url or settings.database_url
replica_connect_args, replica_pool_kwargs = _build_engine_and_pool_kwargs(
    replica_url, is_replica=True
)
replica_engine: AsyncEngine = create_async_engine(
    replica_url,
    echo=settings.db_echo,
    connect_args=replica_connect_args,
    **replica_pool_kwargs,
)

# Backwards-compatible primary engine alias
engine: AsyncEngine = primary_engine


class Base(AsyncAttrs, DeclarativeBase):
    """Modern SQLAlchemy 2.0 declarative base incorporating async attribute management.

    Subclasses inherit AsyncAttrs enabling explicit async relationship loading
    and async property evaluation without greenlet context errors.
    """

    pass


# Primary & Replica Session Factories
PrimaryAsyncSession: async_sessionmaker[AsyncSession] = async_sessionmaker(
    bind=primary_engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)

ReplicaAsyncSession: async_sessionmaker[AsyncSession] = async_sessionmaker(
    bind=replica_engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)

# Backwards-compatible session factory alias
async_session_factory: async_sessionmaker[AsyncSession] = PrimaryAsyncSession


async def get_db_session() -> AsyncGenerator[AsyncSession]:
    """Yield an active asynchronous database session with automatic transaction lifecycle.

    Pre-yield: Opens session from async_session_factory in O(1) time.
    Post-yield:
      - Automatically commits transaction on clean execution.
      - Rolls back transaction upon any unhandled exception.
      - Always closes session in finally block to avoid connection pool leakage.
    """
    session: AsyncSession = async_session_factory()
    try:
        yield session
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()


def _extract_pool_metrics(target_engine: AsyncEngine) -> dict[str, Any]:
    """Extract real-time telemetry metrics from a targeted database connection pool."""
    pool = target_engine.pool
    pool_name = type(pool).__name__
    clean_pool_type = "QueuePool" if "QueuePool" in pool_name else pool_name

    checked_in = pool.checkedin() if hasattr(pool, "checkedin") and callable(pool.checkedin) else 0
    checked_out = pool.checkedout() if hasattr(pool, "checkedout") and callable(pool.checkedout) else 0
    raw_overflow = pool.overflow() if hasattr(pool, "overflow") and callable(pool.overflow) else 0
    overflow_conns = max(0, raw_overflow)
    pool_size = pool.size() if hasattr(pool, "size") and callable(pool.size) else 0

    return {
        "pool_type": clean_pool_type,
        "pool_size": pool_size,
        "checked_in_connections": checked_in,
        "checked_out_connections": checked_out,
        "overflow_connections": overflow_conns,
        "total_open_connections": checked_in + checked_out,
        "statement_timeout_ms": settings.db_statement_timeout_ms,
        "idle_in_transaction_timeout_ms": settings.db_idle_in_transaction_timeout_ms,
        "lock_timeout_ms": settings.db_lock_timeout_ms,
        "pool_timeout_seconds": settings.db_pool_timeout,
        "pool_recycle_seconds": settings.db_pool_recycle,
    }


def get_db_pool_status() -> dict[str, Any]:
    """Inspect and return current primary database connection pool telemetry."""
    return _extract_pool_metrics(primary_engine)


def get_dual_db_pool_status() -> dict[str, Any]:
    """Inspect and return operational telemetry for both Primary and Replica connection pools."""
    return {
        "primary": _extract_pool_metrics(primary_engine),
        "replica": _extract_pool_metrics(replica_engine),
    }


# Register SQLite dialect test compatibility hooks
@event.listens_for(primary_engine.sync_engine, "connect")
def _register_sqlite_functions(dbapi_connection: Any, connection_record: Any) -> None:
    if hasattr(dbapi_connection, "create_function"):
        import time

        dbapi_connection.create_function("pg_sleep", 1, time.sleep)


@event.listens_for(replica_engine.sync_engine, "connect")
def _register_replica_sqlite_functions(dbapi_connection: Any, connection_record: Any) -> None:
    if hasattr(dbapi_connection, "create_function"):
        import time

        dbapi_connection.create_function("pg_sleep", 1, time.sleep)


def apply_migrations(alembic_ini_path: str = "alembic.ini", revision: str = "head") -> None:
    """Programmatically run Alembic migrations up to the specified revision."""
    from alembic import command
    from alembic.config import Config

    alembic_cfg = Config(alembic_ini_path)
    alembic_cfg.set_main_option("sqlalchemy.url", settings.database_url)
    command.upgrade(alembic_cfg, revision)


def rollback_migration(alembic_ini_path: str = "alembic.ini", revision: str = "-1") -> None:
    """Programmatically downgrade Alembic migrations to the specified revision."""
    from alembic import command
    from alembic.config import Config

    alembic_cfg = Config(alembic_ini_path)
    alembic_cfg.set_main_option("sqlalchemy.url", settings.database_url)
    command.downgrade(alembic_cfg, revision)
