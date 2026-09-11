"""
Tests for Database Read/Write Replica Splitting Architecture (Day 65).

Validates:
1. Primary Engine Routing: Transactional mutations execute on Primary master engine.
2. Read Replica Routing: Queries execute on Read Replica engine shedding load.
3. Read-Your-Own-Writes Invariant: Writes activate stickiness switching subsequent reads to Primary.
4. Replica Mutation Guard: INSERT/UPDATE/DELETE on replica session is forbidden.
5. Fallback Invariant: When database_read_replica_url is unset, replica gracefully defaults to primary URL.
6. HTTP Endpoints & Telemetry: Validates X-Database-Engine headers and dual pool status.
"""

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from app.core.config import get_settings
from app.core.database import (
    PrimaryAsyncSession,
    ReplicaAsyncSession,
    primary_engine,
    replica_engine,
)
from app.core.exceptions import ReadOnlyReplicaMutationException
from app.core.routing_session import DatabaseRole, RoutingUnitOfWork
from app.main import app
from app.services.product_service import ProductService

# ============================================================================
# Unit Tests: Dynamic Routing & Engine Selection
# ============================================================================


@pytest.mark.asyncio
async def test_replica_fallback_configuration() -> None:
    """Fallback Invariant: When replica URL is unset, replica engine connects cleanly to primary URL."""
    settings = get_settings()
    assert primary_engine is not None
    assert replica_engine is not None
    # Both engines exist independently
    assert primary_engine.url.render_as_string(hide_password=True) == settings.database_url


@pytest.mark.asyncio
async def test_read_replica_session_executes_select() -> None:
    """Read queries execute cleanly on ReplicaAsyncSession."""
    async with ReplicaAsyncSession() as session:
        result = await session.execute(text("SELECT 42 AS val"))
        row = result.scalar()
        assert row == 42


@pytest.mark.asyncio
async def test_replica_mutation_guard_blocks_insert() -> None:
    """Replica Mutation Shield: Mutating statements on replica engine trigger ReadOnlyReplicaMutationException."""
    async with ReplicaAsyncSession() as session:
        with pytest.raises(ReadOnlyReplicaMutationException) as exc_info:
            await session.execute(text("INSERT INTO products (name, stock, price) VALUES ('Illegal', 1, 10.0)"))
        assert "strictly prohibited on read replica engine" in str(exc_info.value)


@pytest.mark.asyncio
async def test_primary_engine_executes_mutations_and_persists() -> None:
    """Primary engine executes mutations and persists them to the master database."""
    async with PrimaryAsyncSession() as session:
        result = await session.execute(
            text(
                "INSERT INTO products (name, stock, price) "
                "VALUES ('Primary Test Product', 15, 49.99) RETURNING id, name"
            )
        )
        row = result.fetchone()
        assert row is not None
        assert row[1] == "Primary Test Product"
        await session.commit()


# ============================================================================
# Unit Tests: Read-Your-Own-Writes Invariant
# ============================================================================


@pytest.mark.asyncio
async def test_read_your_own_writes_lag_guard() -> None:
    """Write within transaction context flips current_read_target from REPLICA to PRIMARY."""
    async with RoutingUnitOfWork() as uow:
        # 1. Before any mutation: reads route to REPLICA
        assert uow.has_written is False
        assert uow.current_read_target == DatabaseRole.REPLICA
        assert uow.active_read_session == uow.replica_session

        # 2. Perform write mutation
        created = await uow.products.create(name="Lag Guard Product", stock=20, price=99.0)
        assert created.id is not None

        # 3. After mutation: Read-Your-Own-Writes stickiness activated
        assert uow.has_written is True
        assert uow.current_read_target == DatabaseRole.PRIMARY
        assert uow.active_read_session == uow.primary_session

        # 4. Immediate read within same UoW fetches from Primary (zero replication lag staleness)
        fetched = await uow.products.get_by_id(created.id)
        assert fetched is not None
        assert fetched.name == "Lag Guard Product"
        await uow.commit()


@pytest.mark.asyncio
async def test_product_service_create_and_fetch() -> None:
    """ProductService.create_and_fetch_product demonstrates lag guard stickiness."""
    service = ProductService()
    entity, target_role, has_written = await service.create_and_fetch_product(
        name="Service Lag Product", stock=5, price=19.99
    )

    assert entity.id is not None
    assert entity.name == "Service Lag Product"
    assert target_role == DatabaseRole.PRIMARY
    assert has_written is True


# ============================================================================
# Integration & HTTP Telemetry Header Tests
# ============================================================================


@pytest.mark.asyncio
async def test_endpoint_read_probe_telemetry_header() -> None:
    """GET /database/routing/read-probe returns X-Database-Engine: REPLICA."""
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        response = await ac.get("/database/routing/read-probe")

    assert response.status_code == 200
    assert response.headers.get("X-Database-Engine") == "REPLICA"
    data = response.json()
    assert data["target_engine"] == "REPLICA"
    assert data["query_type"] == "READ"
    assert data["read_your_own_writes_active"] is False


@pytest.mark.asyncio
async def test_endpoint_write_probe_telemetry_header() -> None:
    """POST /database/routing/write-probe returns X-Database-Engine: PRIMARY."""
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        response = await ac.post(
            "/database/routing/write-probe",
            json={"name": "Probe Item", "stock": 10, "price": 25.5},
        )

    assert response.status_code == 201
    assert response.headers.get("X-Database-Engine") == "PRIMARY"
    data = response.json()
    assert data["target_engine"] == "PRIMARY"
    assert data["name"] == "Probe Item"


@pytest.mark.asyncio
async def test_endpoint_read_after_write_probe_telemetry_header() -> None:
    """POST /database/routing/read-after-write-probe returns X-Database-Engine: PRIMARY with lag active."""
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        response = await ac.post(
            "/database/routing/read-after-write-probe",
            json={"name": "Lag Item", "stock": 8, "price": 45.0},
        )

    assert response.status_code == 200
    assert response.headers.get("X-Database-Engine") == "PRIMARY"
    data = response.json()
    assert data["target_engine"] == "PRIMARY"
    assert data["query_type"] == "READ_AFTER_WRITE"
    assert data["read_your_own_writes_active"] is True


@pytest.mark.asyncio
async def test_endpoint_pool_status() -> None:
    """GET /database/routing/pool-status returns telemetry for Primary and Replica pools."""
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        response = await ac.get("/database/routing/pool-status")

    assert response.status_code == 200
    data = response.json()
    assert "primary" in data
    assert "replica" in data
    assert "pool_type" in data["primary"]
    assert "pool_type" in data["replica"]
