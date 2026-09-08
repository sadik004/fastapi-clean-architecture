"""SQLAlchemy 2.0 Asynchronous Database Configuration & Session Management."""

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

# Global asynchronous engine
engine: AsyncEngine = create_async_engine(
    settings.database_url,
    echo=settings.db_echo,
    connect_args=connect_args,
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
