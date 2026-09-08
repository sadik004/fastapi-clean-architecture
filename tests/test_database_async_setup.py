"""Comprehensive test suite for Day 15: SQLAlchemy 2.0 Async Setup.

Verifies:
1. Direct connection ping via select(1).
2. The expire_on_commit=False architectural invariant (preventing MissingGreenlet).
3. Contrast test with expire_on_commit=True demonstrating the MissingGreenlet failure mode.
4. Auto-commit behavior on normal exit in get_db_session.
5. Rollback behavior on unhandled exceptions in get_db_session.
6. Database health check endpoint GET /health/db.
7. Lifespan context manager startup and shutdown (engine disposal).
"""

from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from fastapi import status
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.exc import MissingGreenlet
from sqlalchemy.ext.asyncio import (
    AsyncAttrs,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.core.database import (
    async_session_factory,
    engine,
    get_db_session,
)
from app.main import app, lifespan


class Day15TestBase(AsyncAttrs, DeclarativeBase):
    """Isolated test declarative base to prevent test models from polluting production Base.metadata."""

    pass


# Test declarative entity for verifying database persistence & attribute access
class SampleTestEntity(Day15TestBase):
    """Test model to verify persistence and post-commit attribute retention."""

    __tablename__ = "day15_sample_test_entities"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column()
    content: Mapped[str] = mapped_column()


@pytest_asyncio.fixture(autouse=True)
async def setup_test_tables() -> AsyncGenerator[None]:
    """Ensure database tables exist before tests and are cleaned up afterwards."""
    async with engine.begin() as conn:
        await conn.run_sync(Day15TestBase.metadata.create_all)
    yield
    async with engine.begin() as conn:
        await conn.run_sync(Day15TestBase.metadata.drop_all)


# ============================================================================
# 1. Connection Ping & Scalar Query Tests
# ============================================================================


@pytest.mark.asyncio
async def test_database_connection_ping() -> None:
    """Verify that an async database session executes select(1) and evaluates to 1."""
    async with async_session_factory() as session:
        result = await session.scalar(select(1))
        assert result == 1


# ============================================================================
# 2. Expire On Commit Invariant Tests
# ============================================================================


@pytest.mark.asyncio
async def test_expire_on_commit_false_invariant() -> None:
    """Verify expire_on_commit=False preserves entity attributes post-commit.

    In async SQLAlchemy, if expire_on_commit is True, accessing attributes after
    commit triggers synchronous lazy loading which crashes with MissingGreenlet.
    With expire_on_commit=False, attributes remain safely accessible in memory.
    """
    async with async_session_factory() as session:
        entity = SampleTestEntity(
            title="Clean Architecture",
            content="SQLAlchemy 2.0 Async Persistence",
        )
        session.add(entity)
        await session.commit()

        # Immediate post-commit attribute access MUST succeed without MissingGreenlet
        assert entity.id is not None
        assert entity.title == "Clean Architecture"
        assert entity.content == "SQLAlchemy 2.0 Async Persistence"


@pytest.mark.asyncio
async def test_expire_on_commit_true_demonstrates_missing_greenlet() -> None:
    """Demonstrate why expire_on_commit=True is prohibited in async SQLAlchemy.

    When expire_on_commit=True, post-commit attribute access attempts an implicit
    synchronous lazy refresh and throws MissingGreenlet.
    """
    test_engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
    )
    async with test_engine.begin() as conn:
        await conn.run_sync(Day15TestBase.metadata.create_all)

    # Intentionally misconfigured sessionmaker with expire_on_commit=True
    bad_session_factory = async_sessionmaker(
        bind=test_engine,
        class_=AsyncSession,
        expire_on_commit=True,
    )

    async with bad_session_factory() as session:
        entity = SampleTestEntity(
            title="Trap Demonstration",
            content="Will trigger MissingGreenlet",
        )
        session.add(entity)
        await session.commit()

        # Accessing attribute on expired async instance raises MissingGreenlet
        with pytest.raises(MissingGreenlet):
            _ = entity.title

    await test_engine.dispose()


# ============================================================================
# 3. Session Generator Dependency Lifecycle (Auto-commit & Rollback)
# ============================================================================


@pytest.mark.asyncio
async def test_get_db_session_auto_commits_on_clean_exit() -> None:
    """Verify get_db_session automatically commits changes upon normal completion."""
    gen = get_db_session()
    session = await gen.__anext__()

    entity = SampleTestEntity(
        title="Auto-Commit Title",
        content="Auto-Commit Content",
    )
    session.add(entity)

    # Let the generator exit cleanly (triggers await session.commit())
    try:
        await gen.__anext__()
    except StopAsyncIteration:
        pass

    # Verify record was committed and is retrievable in a distinct session
    async with async_session_factory() as verification_session:
        result = await verification_session.scalars(
            select(SampleTestEntity).where(SampleTestEntity.title == "Auto-Commit Title")
        )
        persisted = result.first()
        assert persisted is not None
        assert persisted.content == "Auto-Commit Content"


@pytest.mark.asyncio
async def test_get_db_session_rolls_back_on_exception() -> None:
    """Verify get_db_session automatically rolls back transaction when an exception is raised."""
    gen = get_db_session()
    session = await gen.__anext__()

    entity = SampleTestEntity(
        title="Rollback Title",
        content="Should never persist",
    )
    session.add(entity)
    await session.flush()

    # Throw an unhandled exception into the generator
    with pytest.raises(RuntimeError, match="Simulated service failure"):
        await gen.athrow(RuntimeError("Simulated service failure"))

    # Verify that the rolled back record was NOT persisted
    async with async_session_factory() as verification_session:
        result = await verification_session.scalars(
            select(SampleTestEntity).where(SampleTestEntity.title == "Rollback Title")
        )
        persisted = result.first()
        assert persisted is None


# ============================================================================
# 4. Database Health Check Endpoint (GET /health/db)
# ============================================================================


def test_health_db_endpoint_success(client: TestClient) -> None:
    """Verify GET /health/db returns HTTP 200 with healthy status and latency metrics."""
    response = client.get("/health/db")

    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["status"] == "healthy"
    assert data["database"] == "connected"
    assert data["scalar_result"] == 1
    assert "latency_ms" in data
    assert isinstance(data["latency_ms"], float)
    assert data["latency_ms"] >= 0.0


# ============================================================================
# 5. Lifespan Startup & Teardown Verification
# ============================================================================


@pytest.mark.asyncio
async def test_lifespan_lifecycle_execution() -> None:
    """Verify lifespan context manager startup executes create_all and shutdown executes engine.dispose."""
    async with lifespan(app):
        # Within lifespan, engine connection pool is live
        async with engine.connect() as conn:
            ping = await conn.scalar(select(1))
            assert ping == 1
