"""Router for Database Read/Write Replica Splitting Probes & Telemetry."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_dual_db_pool_status
from app.core.routing_session import (
    DatabaseRole,
    RoutingUnitOfWork,
    get_primary_uow,
    get_read_session,
)
from app.schemas.replica import (
    CreateProductProbeRequest,
    CreateProductProbeResponse,
    DatabaseProbeResponse,
    DualPoolStatusResponse,
)
from app.services.product_service import ProductService

router = APIRouter(prefix="/database/routing", tags=["Database Routing & Replica Splitting"])


@router.get(
    "/read-probe",
    response_model=DatabaseProbeResponse,
    status_code=status.HTTP_200_OK,
    summary="Probe read query execution routed to the Read Replica engine",
    description=(
        "Executes a lightweight read probe against the configured read replica session. "
        "Injects telemetry header 'X-Database-Engine: REPLICA' verifying load shedding from the master."
    ),
)
async def read_probe_endpoint(
    response: Response,
    read_session: Annotated[AsyncSession, Depends(get_read_session)],
) -> DatabaseProbeResponse:
    """Execute read probe against replica engine."""
    result = await read_session.execute(text("SELECT 1 AS probe_val"))
    val = result.scalar()

    response.headers["X-Database-Engine"] = DatabaseRole.REPLICA.value

    return DatabaseProbeResponse(
        target_engine=DatabaseRole.REPLICA.value,
        query_type="READ",
        read_your_own_writes_active=False,
        data={"probe_result": val, "engine_role": "REPLICA"},
        executed_at=datetime.now(UTC),
    )


@router.post(
    "/write-probe",
    response_model=CreateProductProbeResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Probe write mutation execution routed to the Primary Master engine",
    description=(
        "Executes an INSERT operation via RoutingUnitOfWork strictly against the Primary Master engine. "
        "Injects telemetry header 'X-Database-Engine: PRIMARY'."
    ),
)
async def write_probe_endpoint(
    payload: CreateProductProbeRequest,
    response: Response,
    uow: Annotated[RoutingUnitOfWork, Depends(get_primary_uow)],
) -> CreateProductProbeResponse:
    """Execute mutating insert strictly against Primary engine."""
    service = ProductService(uow=uow)
    entity, target_role = await service.create_product(
        name=payload.name,
        stock=payload.stock,
        price=payload.price,
    )

    response.headers["X-Database-Engine"] = target_role.value

    return CreateProductProbeResponse(
        product_id=entity.id,
        name=entity.name,
        stock=entity.stock,
        price=entity.price,
        target_engine=target_role.value,
        executed_at=datetime.now(UTC),
    )


@router.post(
    "/read-after-write-probe",
    response_model=DatabaseProbeResponse,
    status_code=status.HTTP_200_OK,
    summary="Probe Read-Your-Own-Writes lag guard stickiness within a transaction",
    description=(
        "Executes an INSERT followed immediately by a SELECT within the same transaction context. "
        "Proves that despite being a read query, the lag guard sticks to PRIMARY to prevent reading stale replica data."
    ),
)
async def read_after_write_probe_endpoint(
    payload: CreateProductProbeRequest,
    response: Response,
    uow: Annotated[RoutingUnitOfWork, Depends(get_primary_uow)],
) -> DatabaseProbeResponse:
    """Execute write mutation followed by immediate read within same UoW context."""
    service = ProductService(uow=uow)
    entity, target_role, has_written = await service.create_and_fetch_product(
        name=payload.name,
        stock=payload.stock,
        price=payload.price,
    )

    response.headers["X-Database-Engine"] = target_role.value

    return DatabaseProbeResponse(
        target_engine=target_role.value,
        query_type="READ_AFTER_WRITE",
        read_your_own_writes_active=has_written,
        data={
            "product_id": entity.id,
            "name": entity.name,
            "stock": entity.stock,
            "price": entity.price,
            "stickiness_reason": "Replication lag prevention",
        },
        executed_at=datetime.now(UTC),
    )


@router.get(
    "/pool-status",
    response_model=DualPoolStatusResponse,
    status_code=status.HTTP_200_OK,
    summary="Inspect live connection pool telemetry for both Primary and Replica engines",
)
async def pool_status_endpoint() -> DualPoolStatusResponse:
    """Return operational connection metrics for both Primary and Replica engines."""
    status_data = get_dual_db_pool_status()
    return DualPoolStatusResponse(**status_data)


__all__ = ["router"]
