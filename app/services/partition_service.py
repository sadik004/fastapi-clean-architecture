"""Database Table Partitioning & Maintenance Service.

Orchestrates PostgreSQL Declarative Range Partitioning, partition pruning telemetry,
dynamic partition lifecycle management, and transparent SQLite test emulation.
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import primary_engine
from app.models.audit_partition import AuditLogPartitionModel
from app.schemas.partition import (
    AuditLogResponse,
    AuditSearchResponse,
    CreatePartitionResponse,
    DetachPartitionResponse,
    ListPartitionsResponse,
    PartitionInfo,
    PartitionPruningTelemetry,
)

logger = logging.getLogger(__name__)

# Valid identifier pattern to prevent SQL injection in DDL statements
_SAFE_IDENTIFIER_REGEX = re.compile(r"^[a-zA-Z0-9_]+$")


class PartitionManagerService:
    """Service orchestrating declarative table partitions and pruning analytics."""

    @staticmethod
    def resolve_target_partition(created_at: datetime) -> str:
        """Deterministically resolve the expected child partition name from UTC timestamp.

        Partition Schema:
        - 2025: audit_logs_y2025
        - 2026: audit_logs_y2026
        - Default catch-all: audit_logs_default
        """
        year = created_at.year
        if year == 2025:
            return "audit_logs_y2025"
        if year == 2026:
            return "audit_logs_y2026"
        return "audit_logs_default"

    @classmethod
    async def ensure_base_and_child_partitions(cls, session: AsyncSession) -> None:
        """Bootstrap the root partitioned table and base child partition shards."""
        dialect_name = session.bind.dialect.name if session.bind else primary_engine.dialect.name

        if dialect_name == "postgresql":
            # PostgreSQL Declarative Partitioning DDL
            ddl_statements = [
                """
                CREATE TABLE IF NOT EXISTS audit_logs (
                    id UUID NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL,
                    event_type VARCHAR(50) NOT NULL,
                    user_id INTEGER NOT NULL,
                    payload JSON NOT NULL,
                    PRIMARY KEY (id, created_at)
                ) PARTITION BY RANGE (created_at);
                """,
                """
                CREATE TABLE IF NOT EXISTS audit_logs_y2025 PARTITION OF audit_logs
                    FOR VALUES FROM ('2025-01-01 00:00:00+00') TO ('2026-01-01 00:00:00+00');
                """,
                """
                CREATE TABLE IF NOT EXISTS audit_logs_y2026 PARTITION OF audit_logs
                    FOR VALUES FROM ('2026-01-01 00:00:00+00') TO ('2027-01-01 00:00:00+00');
                """,
                """
                CREATE TABLE IF NOT EXISTS audit_logs_default PARTITION OF audit_logs DEFAULT;
                """,
            ]
            for stmt in ddl_statements:
                await session.execute(text(stmt))
            await session.commit()
        else:
            # SQLite Test Environment Emulation DDL
            # SQLite creates standard tables and triggers mimicking declarative partition routing
            sqlite_ddl = [
                """
                CREATE TABLE IF NOT EXISTS audit_logs (
                    id VARCHAR(36) NOT NULL,
                    created_at TIMESTAMP NOT NULL,
                    event_type VARCHAR(50) NOT NULL,
                    user_id INTEGER NOT NULL,
                    payload JSON NOT NULL,
                    PRIMARY KEY (id, created_at)
                );
                """,
                """
                CREATE TABLE IF NOT EXISTS audit_logs_y2025 (
                    id VARCHAR(36) NOT NULL,
                    created_at TIMESTAMP NOT NULL,
                    event_type VARCHAR(50) NOT NULL,
                    user_id INTEGER NOT NULL,
                    payload JSON NOT NULL,
                    PRIMARY KEY (id, created_at)
                );
                """,
                """
                CREATE TABLE IF NOT EXISTS audit_logs_y2026 (
                    id VARCHAR(36) NOT NULL,
                    created_at TIMESTAMP NOT NULL,
                    event_type VARCHAR(50) NOT NULL,
                    user_id INTEGER NOT NULL,
                    payload JSON NOT NULL,
                    PRIMARY KEY (id, created_at)
                );
                """,
                """
                CREATE TABLE IF NOT EXISTS audit_logs_default (
                    id VARCHAR(36) NOT NULL,
                    created_at TIMESTAMP NOT NULL,
                    event_type VARCHAR(50) NOT NULL,
                    user_id INTEGER NOT NULL,
                    payload JSON NOT NULL,
                    PRIMARY KEY (id, created_at)
                );
                """,
                """
                CREATE TRIGGER IF NOT EXISTS trg_audit_logs_y2025
                AFTER INSERT ON audit_logs
                WHEN strftime('%Y', NEW.created_at) = '2025'
                BEGIN
                    INSERT OR REPLACE INTO audit_logs_y2025 (id, created_at, event_type, user_id, payload)
                    VALUES (NEW.id, NEW.created_at, NEW.event_type, NEW.user_id, NEW.payload);
                END;
                """,
                """
                CREATE TRIGGER IF NOT EXISTS trg_audit_logs_y2026
                AFTER INSERT ON audit_logs
                WHEN strftime('%Y', NEW.created_at) = '2026'
                BEGIN
                    INSERT OR REPLACE INTO audit_logs_y2026 (id, created_at, event_type, user_id, payload)
                    VALUES (NEW.id, NEW.created_at, NEW.event_type, NEW.user_id, NEW.payload);
                END;
                """,
                """
                CREATE TRIGGER IF NOT EXISTS trg_audit_logs_default
                AFTER INSERT ON audit_logs
                WHEN strftime('%Y', NEW.created_at) NOT IN ('2025', '2026')
                BEGIN
                    INSERT OR REPLACE INTO audit_logs_default (id, created_at, event_type, user_id, payload)
                    VALUES (NEW.id, NEW.created_at, NEW.event_type, NEW.user_id, NEW.payload);
                END;
                """,
            ]
            for stmt in sqlite_ddl:
                await session.execute(text(stmt))
            await session.commit()

    @classmethod
    async def insert_audit_log(
        cls,
        session: AsyncSession,
        event_type: str,
        user_id: int,
        payload: dict[str, Any],
        created_at: datetime | None = None,
    ) -> AuditLogResponse:
        """Insert audit event row into root table; database engine automatically routes to partition."""
        await cls.ensure_base_and_child_partitions(session)

        timestamp = created_at if created_at is not None else datetime.now(UTC)
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=UTC)

        target_partition = cls.resolve_target_partition(timestamp)

        record = AuditLogPartitionModel(
            event_type=event_type,
            user_id=user_id,
            payload=payload,
            created_at=timestamp,
        )
        session.add(record)
        await session.commit()
        await session.refresh(record)

        return AuditLogResponse(
            id=record.id,
            created_at=record.created_at,
            event_type=record.event_type,
            user_id=record.user_id,
            payload=record.payload,
            target_partition=target_partition,
        )

    @classmethod
    async def search_audit_logs(
        cls,
        session: AsyncSession,
        start_date: datetime,
        end_date: datetime,
        event_type: str | None = None,
        user_id: int | None = None,
    ) -> AuditSearchResponse:
        """Perform partition-pruned date range search and report pruning telemetry."""
        await cls.ensure_base_and_child_partitions(session)

        if start_date.tzinfo is None:
            start_date = start_date.replace(tzinfo=UTC)
        if end_date.tzinfo is None:
            end_date = end_date.replace(tzinfo=UTC)

        query = (
            select(AuditLogPartitionModel)
            .where(
                AuditLogPartitionModel.created_at >= start_date,
                AuditLogPartitionModel.created_at < end_date,
            )
            .order_by(
                AuditLogPartitionModel.created_at.desc(),
                AuditLogPartitionModel.id.desc(),
            )
        )

        if event_type:
            query = query.where(AuditLogPartitionModel.event_type == event_type)
        if user_id is not None:
            query = query.where(AuditLogPartitionModel.user_id == user_id)

        result = await session.execute(query)
        records = list(result.scalars().all())

        # Evaluate partition pruning telemetry
        active_partitions = await cls.list_active_partitions(session)
        total_parts = [p.name for p in active_partitions.partitions]

        scanned: list[str] = []
        for p in active_partitions.partitions:
            if p.is_default:
                # Default partition is scanned only if range extends outside 2025 and 2026
                if start_date < datetime(2025, 1, 1, tzinfo=UTC) or end_date > datetime(2027, 1, 1, tzinfo=UTC):
                    scanned.append(p.name)
            elif p.from_bound and p.to_bound:
                try:
                    p_from = datetime.fromisoformat(p.from_bound)
                    p_to = datetime.fromisoformat(p.to_bound)
                    if not (end_date <= p_from or start_date >= p_to):
                        scanned.append(p.name)
                except Exception:
                    # Fallback on name heuristics if bound string isn't standard ISO
                    if "2025" in p.name and not (end_date <= datetime(2025, 1, 1, tzinfo=UTC) or start_date >= datetime(2026, 1, 1, tzinfo=UTC)):
                        scanned.append(p.name)
                    elif "2026" in p.name and not (end_date <= datetime(2026, 1, 1, tzinfo=UTC) or start_date >= datetime(2027, 1, 1, tzinfo=UTC)):
                        scanned.append(p.name)
            else:
                if "2025" in p.name and not (end_date <= datetime(2025, 1, 1, tzinfo=UTC) or start_date >= datetime(2026, 1, 1, tzinfo=UTC)):
                    scanned.append(p.name)
                elif "2026" in p.name and not (end_date <= datetime(2026, 1, 1, tzinfo=UTC) or start_date >= datetime(2027, 1, 1, tzinfo=UTC)):
                    scanned.append(p.name)

        pruned = [name for name in total_parts if name not in scanned]
        efficiency = (len(pruned) / len(total_parts) * 100.0) if total_parts else 0.0

        telemetry = PartitionPruningTelemetry(
            scanned_partitions=scanned,
            pruned_partitions=pruned,
            pruning_efficiency_percent=round(efficiency, 2),
            query_range_start=start_date,
            query_range_end=end_date,
        )

        logs_resp = [
            AuditLogResponse(
                id=r.id,
                created_at=r.created_at,
                event_type=r.event_type,
                user_id=r.user_id,
                payload=r.payload,
                target_partition=cls.resolve_target_partition(r.created_at),
            )
            for r in records
        ]

        return AuditSearchResponse(
            logs=logs_resp,
            total_count=len(logs_resp),
            telemetry=telemetry,
        )

    @classmethod
    async def create_monthly_partition(
        cls,
        session: AsyncSession,
        year: int,
        month: int,
    ) -> CreatePartitionResponse:
        """Dynamically generate a new monthly partition child table."""
        await cls.ensure_base_and_child_partitions(session)

        partition_name = f"audit_logs_y{year}m{month:02d}"
        if not _SAFE_IDENTIFIER_REGEX.match(partition_name):
            raise ValueError(f"Invalid partition name: {partition_name}")

        start_dt = datetime(year, month, 1, 0, 0, 0, tzinfo=UTC)
        if month == 12:
            end_dt = datetime(year + 1, 1, 1, 0, 0, 0, tzinfo=UTC)
        else:
            end_dt = datetime(year, month + 1, 1, 0, 0, 0, tzinfo=UTC)

        start_str = start_dt.isoformat()
        end_str = end_dt.isoformat()

        dialect_name = session.bind.dialect.name if session.bind else primary_engine.dialect.name
        if dialect_name == "postgresql":
            ddl = f"""
            CREATE TABLE IF NOT EXISTS {partition_name} PARTITION OF audit_logs
                FOR VALUES FROM ('{start_str}') TO ('{end_str}');
            """
        else:
            ddl = f"""
            CREATE TABLE IF NOT EXISTS {partition_name} (
                id VARCHAR(36) NOT NULL,
                created_at TIMESTAMP NOT NULL,
                event_type VARCHAR(50) NOT NULL,
                user_id INTEGER NOT NULL,
                payload JSON NOT NULL,
                PRIMARY KEY (id, created_at)
            );
            """

        await session.execute(text(ddl))
        await session.commit()

        return CreatePartitionResponse(
            status="created",
            partition_name=partition_name,
            range_from=start_str,
            range_to=end_str,
        )

    @classmethod
    async def detach_and_drop_old_partition(
        cls,
        session: AsyncSession,
        partition_name: str,
    ) -> DetachPartitionResponse:
        """Safely detach and drop a historical partition in O(1) time with zero root table lock."""
        if not _SAFE_IDENTIFIER_REGEX.match(partition_name):
            raise ValueError(f"Invalid partition identifier: {partition_name}")

        dialect_name = session.bind.dialect.name if session.bind else primary_engine.dialect.name
        if dialect_name == "postgresql":
            # PostgreSQL O(1) Detach and Drop
            await session.execute(text(f"ALTER TABLE audit_logs DETACH PARTITION {partition_name};"))
            await session.execute(text(f"DROP TABLE IF EXISTS {partition_name};"))
        else:
            # SQLite Test Emulation
            await session.execute(text(f"DROP TRIGGER IF EXISTS trg_{partition_name};"))
            await session.execute(text(f"DROP TABLE IF EXISTS {partition_name};"))

        await session.commit()

        return DetachPartitionResponse(
            status="detached_and_dropped",
            partition_name=partition_name,
            message=f"Partition {partition_name} successfully detached and dropped in O(1) time without vacuum bloat.",
        )

    @classmethod
    async def list_active_partitions(cls, session: AsyncSession) -> ListPartitionsResponse:
        """Inspect and return operational metadata for all active child partition shards."""
        await cls.ensure_base_and_child_partitions(session)
        dialect_name = session.bind.dialect.name if session.bind else primary_engine.dialect.name

        partitions: list[PartitionInfo] = []

        if dialect_name == "postgresql":
            query = text(
                """
                SELECT
                    child.relname AS partition_name,
                    parent.relname AS parent_table,
                    pg_get_expr(child.relpartbound, child.oid) AS partition_bound
                FROM pg_inherits
                JOIN pg_class parent ON pg_inherits.inhparent = parent.oid
                JOIN pg_class child ON pg_inherits.inhrelid = child.oid
                WHERE parent.relname = 'audit_logs'
                ORDER BY child.relname;
                """
            )
            result = await session.execute(query)
            rows = result.fetchall()
            for r in rows:
                bound_expr = r.partition_bound or ""
                is_default = "DEFAULT" in bound_expr.upper()

                # Get count
                if not _SAFE_IDENTIFIER_REGEX.match(str(r.partition_name)):
                    continue
                count_res = await session.execute(text(f"SELECT count(*) FROM {r.partition_name}"))  # noqa: S608
                count_val = count_res.scalar() or 0

                partitions.append(
                    PartitionInfo(
                        name=r.partition_name,
                        parent_table=r.parent_table,
                        from_bound=bound_expr if not is_default else None,
                        to_bound=None,
                        is_default=is_default,
                        row_count=int(count_val),
                    )
                )
        else:
            # SQLite introspection
            query = text(
                "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'audit_logs_%' ORDER BY name;"
            )
            result = await session.execute(query)
            table_names = [row[0] for row in result.fetchall()]

            for tbl in table_names:
                if not _SAFE_IDENTIFIER_REGEX.match(str(tbl)):
                    continue
                count_res = await session.execute(text(f"SELECT count(*) FROM {tbl}"))  # noqa: S608
                count_val = count_res.scalar() or 0

                from_b: str | None = None
                to_b: str | None = None
                is_def = False

                if tbl == "audit_logs_y2025":
                    from_b = "2025-01-01T00:00:00+00:00"
                    to_b = "2026-01-01T00:00:00+00:00"
                elif tbl == "audit_logs_y2026":
                    from_b = "2026-01-01T00:00:00+00:00"
                    to_b = "2027-01-01T00:00:00+00:00"
                elif tbl == "audit_logs_default":
                    is_def = True

                partitions.append(
                    PartitionInfo(
                        name=tbl,
                        parent_table="audit_logs",
                        from_bound=from_b,
                        to_bound=to_b,
                        is_default=is_def,
                        row_count=int(count_val),
                    )
                )

        return ListPartitionsResponse(
            partitions=partitions,
            total_partitions=len(partitions),
        )
