"""Database Administration Service.

Handles connection pool telemetry, statement timeout enforcement,
hanging query simulation, and runaway transaction reaper mechanics.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db_pool_status, primary_engine
from app.core.exceptions import DatabaseQueryTimeoutException
from app.schemas.database_admin import (
    ConnectionPoolMetrics,
    KillHangingQueriesResponse,
    SimulateHangingQueryResponse,
)

logger = logging.getLogger(__name__)


class DatabaseAdminService:
    """Service layer orchestrating database connection lifecycles, timeout safety, and telemetry."""

    @staticmethod
    async def get_connection_pool_metrics(session: AsyncSession) -> ConnectionPoolMetrics:
        """Retrieve real-time telemetry from SQLAlchemy connection pool and DB statistics."""
        pool_stats = get_db_pool_status()
        active_queries = 0

        # Attempt PostgreSQL-specific query inspection if running on PostgreSQL engine
        dialect_name = session.bind.dialect.name if session.bind else primary_engine.dialect.name
        if dialect_name == "postgresql":
            try:
                result = await session.execute(
                    text("SELECT count(*) FROM pg_stat_activity WHERE state = 'active' AND pid != pg_backend_pid()")
                )
                count_val = result.scalar()
                if count_val is not None:
                    active_queries = int(count_val)
            except Exception as e:
                logger.warning("Failed to query pg_stat_activity for active queries count: %s", e)

        return ConnectionPoolMetrics(
            pool_type=str(pool_stats.get("pool_type", "QueuePool")),
            pool_size=int(pool_stats.get("pool_size", settings.db_pool_size)),
            checked_in=int(pool_stats.get("checked_in_connections", 0)),
            checked_out=int(pool_stats.get("checked_out_connections", 0)),
            overflow=int(pool_stats.get("overflow_connections", 0)),
            total_open=int(pool_stats.get("total_open_connections", 0)),
            statement_timeout_ms=int(pool_stats.get("statement_timeout_ms", settings.db_statement_timeout_ms)),
            idle_in_transaction_timeout_ms=int(
                pool_stats.get("idle_in_transaction_timeout_ms", settings.db_idle_in_transaction_timeout_ms)
            ),
            lock_timeout_ms=int(pool_stats.get("lock_timeout_ms", settings.db_lock_timeout_ms)),
            pool_timeout_seconds=float(pool_stats.get("pool_timeout_seconds", settings.db_pool_timeout)),
            pool_recycle_seconds=int(pool_stats.get("pool_recycle_seconds", settings.db_pool_recycle)),
            active_queries_count=active_queries,
        )

    @staticmethod
    async def execute_with_custom_timeout(
        session: AsyncSession,
        sql_query: str,
        timeout_ms: int | None = None,
        params: dict[str, Any] | None = None,
    ) -> Any:
        """Execute a query with statement timeout enforcement.

        If execution exceeds `timeout_ms` (or `settings.db_statement_timeout_ms`),
        or if the database server raises query cancellation, the transaction
        is strictly rolled back to prevent connection leakage, and DatabaseQueryTimeoutException is raised.
        """
        effective_timeout_ms = timeout_ms if timeout_ms is not None else settings.db_statement_timeout_ms
        timeout_seconds = effective_timeout_ms / 1000.0
        start_time = time.perf_counter()

        dialect_name = session.bind.dialect.name if session.bind else primary_engine.dialect.name

        try:
            # If PostgreSQL, set local statement_timeout on the session transaction
            if dialect_name == "postgresql":
                await session.execute(text(f"SET LOCAL statement_timeout = {effective_timeout_ms}"))

            # Enforce async timeout safety cutoff
            result = await asyncio.wait_for(
                session.execute(text(sql_query), params or {}),
                timeout=timeout_seconds,
            )
            return result
        except TimeoutError as exc:
            elapsed_ms = (time.perf_counter() - start_time) * 1000.0
            logger.error(
                "Statement timeout triggered (asyncio.TimeoutError): elapsed=%.2fms, limit=%dms, query=%s",
                elapsed_ms,
                effective_timeout_ms,
                sql_query,
            )
            # CRITICAL ANTI-LEAK: Always rollback to restore connection to pristine state
            await session.rollback()
            raise DatabaseQueryTimeoutException(
                query=sql_query,
                timeout_ms=effective_timeout_ms,
            ) from exc
        except (OperationalError, DBAPIError) as exc:
            elapsed_ms = (time.perf_counter() - start_time) * 1000.0
            err_msg = str(exc).lower()
            if "canceling statement due to statement timeout" in err_msg or "querycancelederror" in err_msg:
                logger.error(
                    "Database server canceled query due to statement_timeout: elapsed=%.2fms, query=%s",
                    elapsed_ms,
                    sql_query,
                )
                await session.rollback()
                raise DatabaseQueryTimeoutException(
                    query=sql_query,
                    timeout_ms=effective_timeout_ms,
                ) from exc
            # If other operational/DBAPI error occurs, rollback and re-raise
            await session.rollback()
            raise
        except Exception:
            await session.rollback()
            raise

    @staticmethod
    async def simulate_hanging_query(
        session: AsyncSession,
        duration_seconds: float,
        timeout_ms: int | None = None,
    ) -> SimulateHangingQueryResponse:
        """Simulate a hanging or slow database query (e.g. pg_sleep(N)) and verify timeout cancellation."""
        effective_timeout_ms = timeout_ms if timeout_ms is not None else settings.db_statement_timeout_ms
        sql_query = f"SELECT pg_sleep({duration_seconds})"

        start_time = time.perf_counter()
        try:
            await DatabaseAdminService.execute_with_custom_timeout(
                session=session,
                sql_query=sql_query,
                timeout_ms=effective_timeout_ms,
            )
            elapsed_ms = (time.perf_counter() - start_time) * 1000.0
            return SimulateHangingQueryResponse(
                status="completed",
                executed_query=sql_query,
                duration_seconds=duration_seconds,
                cancelled=False,
                execution_time_ms=round(elapsed_ms, 2),
            )
        except DatabaseQueryTimeoutException:
            # Re-raise so that the exception handler catches it and returns HTTP 504
            raise

    @staticmethod
    async def kill_hanging_queries(
        session: AsyncSession,
        max_age_seconds: int = 10,
    ) -> KillHangingQueriesResponse:
        """Administrative reaper terminating lingering hanging queries running longer than max_age_seconds."""
        dialect_name = session.bind.dialect.name if session.bind else primary_engine.dialect.name
        terminated_count = 0
        details: list[str] = []

        if dialect_name == "postgresql":
            try:
                # Find and terminate backend queries exceeding age threshold
                query = text(
                    """
                    SELECT pid, query, EXTRACT(EPOCH FROM (now() - query_start))::integer AS age_sec,
                           pg_terminate_backend(pid) AS terminated
                    FROM pg_stat_activity
                    WHERE state != 'idle'
                      AND pid != pg_backend_pid()
                      AND query_start < now() - (interval '1 second' * :max_age)
                    """
                )
                result = await session.execute(query, {"max_age": max_age_seconds})
                rows = result.fetchall()
                for row in rows:
                    if row.terminated:
                        terminated_count += 1
                        details.append(f"PID {row.pid} terminated (Age: {row.age_sec}s, Query: {row.query[:60]}...)")
                await session.commit()
            except Exception as exc:
                logger.error("Error executing pg_terminate_backend reap: %s", exc)
                await session.rollback()
        else:
            # SQLite / Test dialect
            details.append(
                f"SQLite dialect does not support remote backend process termination. Scanned age > {max_age_seconds}s."
            )

        return KillHangingQueriesResponse(
            terminated_count=terminated_count,
            target_max_age_seconds=max_age_seconds,
            details=details,
        )
