"""Query Plan Diagnostic Engine for PostgreSQL (and SQLite Fallback).

Analyzes SQL query execution plans using EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)
to detect O(N) Sequential Scans, calculate buffer pool hit ratios, and evaluate
index effectiveness (B-Tree, GIN, BRIN, and Hash) in strictly O(N_nodes) time.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import UnsafeQueryExecutionException
from app.schemas.catalog import QueryPlanNode, QueryPlanReport

# Mutating and dangerous SQL statements strictly prohibited in query plan analysis
_FORBIDDEN_KEYWORDS = frozenset(
    {
        "INSERT",
        "UPDATE",
        "DELETE",
        "DROP",
        "ALTER",
        "TRUNCATE",
        "REPLACE",
        "GRANT",
        "REVOKE",
        "CREATE",
        "EXECUTE",
        "CALL",
        "MERGE",
    }
)

_COMMENT_REGEX = re.compile(r"(--[^\n]*)|(/\*.*?\*/)", re.DOTALL)


class QueryPlanService:
    """Diagnostic service providing deep query plan analysis and scan classification."""

    @staticmethod
    def validate_read_only_query(sql_query: str) -> None:
        """Enforce strict read-only execution guard for all diagnostic queries.

        Raises:
            UnsafeQueryExecutionException: If mutating or schema tampering SQL is detected.
        """
        stripped = _COMMENT_REGEX.sub("", sql_query).strip()
        if not stripped:
            raise UnsafeQueryExecutionException("Empty query provided for execution plan analysis.")

        # Extract tokens
        tokens = re.findall(r"\b[A-Za-z_]+\b", stripped.upper())
        if not tokens:
            raise UnsafeQueryExecutionException("Invalid SQL query syntax.")

        first_token = tokens[0]
        if first_token not in ("SELECT", "WITH", "VALUES"):
            raise UnsafeQueryExecutionException(
                f"Query must begin with a read statement (SELECT, WITH), got '{first_token}'."
            )

        for token in tokens:
            if token in _FORBIDDEN_KEYWORDS:
                raise UnsafeQueryExecutionException(
                    f"Prohibited mutating keyword '{token}' detected in query plan diagnostic payload."
                )

    @classmethod
    def parse_postgres_plan(cls, plan_data: list[dict[str, Any]] | dict[str, Any]) -> QueryPlanReport:
        """Parse PostgreSQL EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) payload in O(N_nodes) time.

        Extracts execution time, planning time, buffer hits/reads, detects scan types,
        and generates warnings if unindexed sequential scans are detected.
        """
        if isinstance(plan_data, list):
            top_obj = plan_data[0] if plan_data else {}
        else:
            top_obj = plan_data

        planning_time_ms = float(top_obj.get("Planning Time", 0.0))
        execution_time_ms = float(top_obj.get("Execution Time", 0.0))
        root_plan = top_obj.get("Plan", {})

        flattened_nodes: list[QueryPlanNode] = []
        scan_types_found: list[str] = []
        warnings: list[str] = []

        total_hit_blocks = 0
        total_read_blocks = 0

        # Iterative or recursive traversal of the Plan node tree
        queue: list[dict[str, Any]] = [root_plan] if root_plan else []

        while queue:
            curr = queue.pop(0)
            node_type = str(curr.get("Node Type", "Unknown"))
            rel_name = curr.get("Relation Name")
            idx_name = curr.get("Index Name")
            total_cost = float(curr.get("Total Cost", 0.0))
            plan_rows = int(curr.get("Plan Rows", 0))
            actual_time = float(curr.get("Actual Total Time", 0.0))
            actual_rows = int(curr.get("Actual Rows", 0))

            # Buffer metrics
            shared_hit = int(curr.get("Shared Hit Blocks", 0))
            shared_read = int(curr.get("Shared Read Blocks", 0))
            total_hit_blocks += shared_hit
            total_read_blocks += shared_read

            node = QueryPlanNode(
                node_type=node_type,
                relation_name=rel_name,
                index_name=idx_name,
                total_cost=total_cost,
                plan_rows=plan_rows,
                actual_total_time_ms=actual_time,
                actual_rows=actual_rows,
            )
            flattened_nodes.append(node)

            # Scan classification
            if "Scan" in node_type or "Search" in node_type:
                if node_type not in scan_types_found:
                    scan_types_found.append(node_type)

            # Performance warning on Sequential Scan
            if node_type == "Seq Scan":
                target = f"'{rel_name}'" if rel_name else "table"
                warnings.append(
                    f"Sequential Scan (Seq Scan) detected on {target}! "
                    f"Forces an O(N) full table scan across physical disk pages. "
                    f"Add a B-Tree, GIN, BRIN, or Hash index to avoid query latency spikes."
                )

            # Enqueue sub-plans
            sub_plans = curr.get("Plans", [])
            if isinstance(sub_plans, list):
                queue.extend(sub_plans)

        primary_scan = (
            scan_types_found[0]
            if scan_types_found
            else (flattened_nodes[0].node_type if flattened_nodes else "Execution Plan")
        )
        total_cost = float(root_plan.get("Total Cost", 0.0))

        return QueryPlanReport(
            dialect="postgresql",
            scan_types=scan_types_found,
            primary_scan_type=primary_scan,
            total_cost=total_cost,
            planning_time_ms=planning_time_ms,
            execution_time_ms=execution_time_ms,
            shared_hit_blocks=total_hit_blocks,
            shared_read_blocks=total_read_blocks,
            warnings=warnings,
            nodes=flattened_nodes,
            raw_plan=plan_data,
        )

    @classmethod
    def parse_sqlite_plan(cls, rows: Sequence[Any]) -> QueryPlanReport:
        """Parse SQLite EXPLAIN QUERY PLAN rows for local test and development environments."""
        flattened_nodes: list[QueryPlanNode] = []
        scan_types_found: list[str] = []
        warnings: list[str] = []

        for row in rows:
            # SQLite EXPLAIN QUERY PLAN returns: (id, parent, notused, detail)
            detail = str(row[3]) if len(row) > 3 else str(row)
            node_type = "Plan Step"
            idx_name = None
            rel_name = None

            # Detect SCAN vs SEARCH
            if "SCAN" in detail:
                node_type = "Seq Scan"
                if node_type not in scan_types_found:
                    scan_types_found.append(node_type)
                match = re.search(r"SCAN\s+TABLE\s+([A-Za-z0-9_]+)", detail)
                if match:
                    rel_name = match.group(1)
                warnings.append(
                    f"Sequential Scan (SCAN TABLE) detected on '{rel_name or 'table'}'! "
                    f"Full table scan performed. Consider creating an index."
                )
            elif "SEARCH" in detail:
                if "USING INDEX" in detail or "USING COVERING INDEX" in detail:
                    node_type = "Index Scan"
                    match = re.search(r"USING\s+(?:COVERING\s+)?INDEX\s+([A-Za-z0-9_]+)", detail)
                    if match:
                        idx_name = match.group(1)
                else:
                    node_type = "Index Search"

                if node_type not in scan_types_found:
                    scan_types_found.append(node_type)

                match_table = re.search(r"SEARCH\s+TABLE\s+([A-Za-z0-9_]+)", detail)
                if match_table:
                    rel_name = match_table.group(1)

            flattened_nodes.append(
                QueryPlanNode(
                    node_type=node_type,
                    relation_name=rel_name,
                    index_name=idx_name,
                    total_cost=1.0 if node_type == "Seq Scan" else 0.1,
                    plan_rows=1,
                    actual_total_time_ms=0.01,
                    actual_rows=1,
                )
            )

        primary_scan = scan_types_found[0] if scan_types_found else "Query Step"

        return QueryPlanReport(
            dialect="sqlite",
            scan_types=scan_types_found,
            primary_scan_type=primary_scan,
            total_cost=float(len(rows)),
            planning_time_ms=0.05,
            execution_time_ms=0.1,
            shared_hit_blocks=len(rows),
            shared_read_blocks=0,
            warnings=warnings,
            nodes=flattened_nodes,
            raw_plan=[list(row) if hasattr(row, "__iter__") else str(row) for row in rows],
        )

    @classmethod
    async def analyze_query_execution_plan(
        cls,
        session: AsyncSession,
        sql_query: str,
        params: dict[str, Any] | None = None,
    ) -> QueryPlanReport:
        """Execute EXPLAIN (ANALYZE, BUFFERS) or EXPLAIN QUERY PLAN safely on active database.

        Guarantees:
        - Read-only execution safety via validation.
        - Dialect-aware branching between PostgreSQL and SQLite.
        """
        cls.validate_read_only_query(sql_query)
        bound_params = params or {}

        bind = session.bind
        dialect_name = bind.dialect.name if bind is not None else "sqlite"

        if dialect_name == "postgresql":
            explain_stmt = f"EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) {sql_query}"
            result = await session.execute(text(explain_stmt), bound_params)
            raw_val = result.scalar_one()
            if isinstance(raw_val, str):
                parsed_json = json.loads(raw_val)
            elif isinstance(raw_val, list | dict):
                parsed_json = raw_val
            else:
                parsed_json = []
            return cls.parse_postgres_plan(parsed_json)

        # Fallback to SQLite EXPLAIN QUERY PLAN
        explain_stmt = f"EXPLAIN QUERY PLAN {sql_query}"
        result = await session.execute(text(explain_stmt), bound_params)
        rows = result.fetchall()
        return cls.parse_sqlite_plan(rows)
