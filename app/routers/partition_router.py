"""Audit Log & Table Partitioning Router.

Provides endpoints for writing audit events into partitioned tables, executing
partition-pruned range queries, and managing partition shard lifecycles.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Path, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db_session
from app.schemas.partition import (
    AuditLogResponse,
    AuditSearchResponse,
    CreateAuditLogRequest,
    CreateMonthlyPartitionRequest,
    CreatePartitionResponse,
    DetachPartitionResponse,
    ListPartitionsResponse,
)
from app.services.partition_service import PartitionManagerService

router = APIRouter(
    prefix="/audit",
    tags=["Audit Logs & Table Partitioning"],
)


@router.post(
    "/partitioned",
    response_model=AuditLogResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Append Partitioned Audit Log Record",
    description=(
        "Inserts an audit event into the root partitioned 'audit_logs' table. "
        "The database engine automatically routes the row to the appropriate "
        "range partition based on its UTC timestamp."
    ),
)
async def create_partitioned_audit_log(
    payload: CreateAuditLogRequest,
    session: AsyncSession = Depends(get_db_session),
) -> AuditLogResponse:
    """Create a new audit event and route to target partition shard."""
    return await PartitionManagerService.insert_audit_log(
        session=session,
        event_type=payload.event_type,
        user_id=payload.user_id,
        payload=payload.payload,
        created_at=payload.created_at,
    )


@router.get(
    "/partitioned/search",
    response_model=AuditSearchResponse,
    status_code=status.HTTP_200_OK,
    summary="Search Audit Logs with Partition Pruning",
    description=(
        "Executes a date-bounded range query on the partitioned audit table. "
        "Returns matching records along with real-time partition pruning telemetry "
        "indicating which child tables were scanned and which were pruned."
    ),
)
async def search_partitioned_audit_logs(
    start_date: datetime = Query(..., description="Range start timestamp (ISO 8601 UTC)"),
    end_date: datetime = Query(..., description="Range end timestamp (ISO 8601 UTC)"),
    event_type: str | None = Query(None, description="Optional filter by event category"),
    user_id: int | None = Query(None, ge=1, description="Optional filter by actor user ID"),
    session: AsyncSession = Depends(get_db_session),
) -> AuditSearchResponse:
    """Search audit logs within date range, leveraging partition pruning."""
    return await PartitionManagerService.search_audit_logs(
        session=session,
        start_date=start_date,
        end_date=end_date,
        event_type=event_type,
        user_id=user_id,
    )


@router.get(
    "/partitions",
    response_model=ListPartitionsResponse,
    status_code=status.HTTP_200_OK,
    summary="List Active Database Table Partitions",
    description="Inspects database catalogs and returns all active child partition shards, bounds, and row counts.",
)
async def list_active_partitions(
    session: AsyncSession = Depends(get_db_session),
) -> ListPartitionsResponse:
    """List operational metadata for all active table partition shards."""
    return await PartitionManagerService.list_active_partitions(session=session)


@router.post(
    "/partitions/monthly",
    response_model=CreatePartitionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Dynamically Create Monthly Partition",
    description="Generates and attaches a new child range partition table for the specified year and month.",
)
async def create_monthly_partition(
    payload: CreateMonthlyPartitionRequest,
    session: AsyncSession = Depends(get_db_session),
) -> CreatePartitionResponse:
    """Dynamically allocate and attach a new monthly partition shard."""
    return await PartitionManagerService.create_monthly_partition(
        session=session,
        year=payload.year,
        month=payload.month,
    )


@router.delete(
    "/partitions/{partition_name}",
    response_model=DetachPartitionResponse,
    status_code=status.HTTP_200_OK,
    summary="Detach and Drop Historical Partition in O(1) Time",
    description=(
        "Performs an O(1) partition detachment via DDL ('ALTER TABLE ... DETACH PARTITION'). "
        "Safely removes historical data without row-by-row locking, transaction log bloat, or VACUUM delays."
    ),
)
async def detach_and_drop_partition(
    partition_name: str = Path(..., description="Exact identifier of the partition table to detach and drop"),
    session: AsyncSession = Depends(get_db_session),
) -> DetachPartitionResponse:
    """Detach and drop a table partition shard in constant O(1) time."""
    return await PartitionManagerService.detach_and_drop_old_partition(
        session=session,
        partition_name=partition_name,
    )
