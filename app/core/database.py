"""SQLAlchemy 2.0 Asynchronous Database Configuration, Connection Pooling & Session Management."""

from collections.abc import AsyncGenerator
from typing import Any
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

# Engine-level connection arguments (SQLite multi-thread compatibility)
connect_args: dict[str, Any] = {}
if settings.database_url.startswith("sqlite"):
    connect_args["check_same_thread"] = False

# Connection pool configuration
# Universal parameters: pool_pre_ping and pool_recycle apply across all dialects and pool classes
pool_kwargs: dict[str, Any] = {
    "pool_pre_ping": settings.db_pool_pre_ping,
    "pool_recycle": settings.db_pool_recycle,
}

# QueuePool parameters (pool_size, max_overflow, pool_timeout):
# Supported by PostgreSQL, MySQL, and file-based SQLite (AsyncAdaptedQueuePool).
# StaticPool (:memory:) does not accept queue size bounds.
is_sqlite_memory = ":memory:" in settings.database_url or "mode=memory" in settings.database_url
if not is_sqlite_memory:
    pool_kwargs["pool_size"] = settings.db_pool_size
    pool_kwargs["max_overflow"] = settings.db_max_overflow
    pool_kwargs["pool_timeout"] = settings.db_pool_timeout

# Global asynchronous engine with enterprise connection pooling
engine: AsyncEngine = create_async_engine(
    settings.database_url,
    echo=settings.db_echo,
    connect_args=connect_args,
    **pool_kwargs,
)


class Base(AsyncAttrs, DeclarativeBase):
    """Modern SQLAlchemy 2.0 declarative base incorporating async attribute management.

    Subclasses inherit AsyncAttrs enabling explicit async relationship loading
    and async property evaluation without greenlet context errors.
    """

    pass


# Asynchronous session factory configured with expire_on_commit=False
# CRITICAL ARCHITECTURAL RULE: expire_on_commit=False is mandatory in async
# SQLAlchemy to prevent implicit synchronous lazy loading and MissingGreenlet crashes.
async_session_factory: async_sessionmaker[AsyncSession] = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
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


def get_db_pool_status() -> dict[str, Any]:
    """Inspect and return current database connection pool telemetry.

    Extracts active metrics from the underlying pool in O(1) time:
    - pool_type: Clean class name of the pool (e.g. QueuePool).
    - pool_size: Maximum baseline configured pool size.
    - checked_in_connections: Idle warm connections currently waiting in the pool.
    - checked_out_connections: Connections currently acquired by active requests/sessions.
    - overflow_connections: Active connections allocated beyond pool_size.
    - total_open_connections: Sum of checked-in and checked-out connections.
    """
    pool = engine.pool
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
    }
