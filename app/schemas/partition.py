"""Pydantic v2 schemas for Partitioned Audit Logs and Database Table Partition Management."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class CreateAuditLogRequest(BaseModel):
    """Payload to create an audit event record."""

    event_type: str = Field(
        ...,
        min_length=1,
        max_length=50,
        description="Audit event identifier category (e.g. USER_LOGIN, PAYMENT_SUCCESS)",
        examples=["ORDER_PLACED"],
    )
    user_id: int = Field(
        ...,
        ge=1,
        description="Target user ID initiating or involved in the action",
        examples=[42],
    )
    payload: dict[str, Any] = Field(
        default_factory=dict,
        description="Arbitrary structured audit payload metadata",
    )
    created_at: datetime | None = Field(
        default=None,
        description="Optional timestamp override. If None, current UTC time is used.",
    )


class AuditLogResponse(BaseModel):
    """Standardized response representation of an audit log entity."""

    id: uuid.UUID = Field(..., description="Unique UUIDv7 event identifier")
    created_at: datetime = Field(..., description="Partition key UTC timestamp")
    event_type: str = Field(..., description="Audit event category")
    user_id: int = Field(..., description="Actor user ID")
    payload: dict[str, Any] = Field(..., description="Audit event payload")
    target_partition: str | None = Field(
        default=None,
        description="Physical child partition shard hosting this record",
    )

    model_config = ConfigDict(from_attributes=True)


class PartitionPruningTelemetry(BaseModel):
    """Diagnostic telemetry reporting partition pruning efficiency for range queries."""

    scanned_partitions: list[str] = Field(
        ...,
        description="List of child partitions actually accessed by the execution planner",
    )
    pruned_partitions: list[str] = Field(
        ...,
        description="List of child partitions pruned (skipped) by the database planner",
    )
    pruning_efficiency_percent: float = Field(
        ...,
        description="Percentage of partitions pruned out of total partitions",
    )
    query_range_start: datetime = Field(..., description="Query start timestamp")
    query_range_end: datetime = Field(..., description="Query end timestamp")


class AuditSearchResponse(BaseModel):
    """Response envelope for partition-pruned date-range audit queries."""

    logs: list[AuditLogResponse] = Field(..., description="Matching audit records")
    total_count: int = Field(..., description="Total matching logs count in returned slice")
    telemetry: PartitionPruningTelemetry = Field(..., description="Partition pruning efficiency metrics")


class PartitionInfo(BaseModel):
    """Structural metadata describing an active database table partition."""

    name: str = Field(..., description="Name of the child partition table")
    parent_table: str = Field(..., description="Name of the root partitioned table")
    from_bound: str | None = Field(None, description="Lower bound of the partition range")
    to_bound: str | None = Field(None, description="Upper bound of the partition range")
    is_default: bool = Field(False, description="Whether this is the catch-all DEFAULT partition")
    row_count: int = Field(0, description="Estimated or exact row count residing in this partition")


class ListPartitionsResponse(BaseModel):
    """Telemetry envelope listing active table partition shards."""

    partitions: list[PartitionInfo] = Field(..., description="Active child partition metadata")
    total_partitions: int = Field(..., description="Total count of active child partition tables")


class CreateMonthlyPartitionRequest(BaseModel):
    """Payload to dynamically create a new monthly partition child table."""

    year: int = Field(..., ge=2020, le=2050, description="Target calendar year", examples=[2027])
    month: int = Field(..., ge=1, le=12, description="Target calendar month", examples=[3])


class CreatePartitionResponse(BaseModel):
    """Response confirming dynamic partition table generation."""

    status: str = Field(..., description="Outcome status (e.g. created, already_exists)")
    partition_name: str = Field(..., description="Name of the created partition table")
    range_from: str = Field(..., description="Lower bound expression")
    range_to: str = Field(..., description="Upper bound expression")


class DetachPartitionResponse(BaseModel):
    """Response confirming O(1) partition detachment."""

    status: str = Field(..., description="Outcome status")
    partition_name: str = Field(..., description="Detached partition table name")
    message: str = Field(..., description="Descriptive status message")
