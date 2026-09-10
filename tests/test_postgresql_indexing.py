"""Comprehensive Test Suite for Day 66: PostgreSQL Indexing & EXPLAIN Plan Diagnostics.

Validates:
1. CatalogItemModel ORM mapping with B-Tree, Composite B-Tree, GIN, BRIN, and Hash indices.
2. Safe query execution guard rejecting mutating SQL statements (INSERT/UPDATE/DELETE/DROP).
3. QueryPlanService PostgreSQL JSON plan parsing in O(N_nodes) time.
4. Detection of Sequential Scans (Seq Scan) and generation of architectural warnings.
5. Detection of Index Scan and Bitmap Index Scan with buffer pool hit/read accounting.
6. API Endpoints: /catalog/items, /catalog/search/tags, and /catalog/diagnostics/explain.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.database import async_session_factory
from app.core.exceptions import UnsafeQueryExecutionException
from app.models.catalog_item import CatalogItemModel
from app.schemas.catalog import QueryPlanReport
from app.services.query_plan_service import QueryPlanService


@pytest.mark.asyncio
async def test_catalog_item_creation_and_persistence() -> None:
    """Verify CatalogItemModel persists and retrieves across multi-index column archetypes."""
    test_sku = f"SKU-{datetime.now(UTC).timestamp()}"
    test_barcode = f"BAR-{datetime.now(UTC).timestamp()}"

    async with async_session_factory() as session:
        item = CatalogItemModel(
            sku=test_sku,
            name="Wireless Noise-Cancelling Headphones",
            category="electronics",
            price=299.99,
            barcode=test_barcode,
            metadata_json={"brand": "Sony", "model": "WH-1000XM5", "anc": True},
            tags=["audio", "wireless", "anc"],
        )
        session.add(item)
        await session.commit()
        await session.refresh(item)

        assert item.id is not None
        assert item.sku == test_sku
        assert item.category == "electronics"
        assert item.price == 299.99
        assert item.metadata_json["brand"] == "Sony"
        assert "anc" in item.tags
        assert item.created_at is not None

        # Clean up
        await session.delete(item)
        await session.commit()


@pytest.mark.asyncio
async def test_btree_composite_range_filtering() -> None:
    """Verify B-Tree composite index columns (category, price) filter range queries cleanly."""
    async with async_session_factory() as session:
        item1 = CatalogItemModel(
            sku=f"SKU-BTREE-1-{datetime.now(UTC).timestamp()}",
            name="Budget Phone",
            category="smartphones",
            price=199.0,
            barcode=f"BAR-1-{datetime.now(UTC).timestamp()}",
            metadata_json={"ram_gb": 4},
            tags=["phone", "budget"],
        )
        item2 = CatalogItemModel(
            sku=f"SKU-BTREE-2-{datetime.now(UTC).timestamp()}",
            name="Flagship Phone",
            category="smartphones",
            price=999.0,
            barcode=f"BAR-2-{datetime.now(UTC).timestamp()}",
            metadata_json={"ram_gb": 16},
            tags=["phone", "flagship"],
        )
        session.add_all([item1, item2])
        await session.commit()

        # Query range: smartphones between 100 and 500
        stmt = (
            select(CatalogItemModel)
            .where(CatalogItemModel.category == "smartphones")
            .where(CatalogItemModel.price >= 100.0)
            .where(CatalogItemModel.price <= 500.0)
        )
        result = await session.execute(stmt)
        items = list(result.scalars().all())

        assert len(items) == 1
        assert items[0].sku == item1.sku

        # Cleanup
        await session.delete(item1)
        await session.delete(item2)
        await session.commit()


def test_query_plan_parser_detects_seq_scan_and_warns() -> None:
    """Verify parse_postgres_plan detects Seq Scan and emits architectural warning."""
    sample_seq_scan_plan = [
        {
            "Plan": {
                "Node Type": "Seq Scan",
                "Parallel Aware": False,
                "Async Capable": False,
                "Relation Name": "catalog_items",
                "Alias": "catalog_items",
                "Startup Cost": 0.0,
                "Total Cost": 35.5,
                "Plan Rows": 1000,
                "Plan Width": 340,
                "Actual Startup Time": 0.015,
                "Actual Total Time": 1.25,
                "Actual Rows": 1000,
                "Actual Loops": 1,
                "Shared Hit Blocks": 12,
                "Shared Read Blocks": 5,
            },
            "Planning Time": 0.15,
            "Execution Time": 1.35,
        }
    ]

    report = QueryPlanService.parse_postgres_plan(sample_seq_scan_plan)
    assert isinstance(report, QueryPlanReport)
    assert report.dialect == "postgresql"
    assert "Seq Scan" in report.scan_types
    assert report.primary_scan_type == "Seq Scan"
    assert report.total_cost == 35.5
    assert report.planning_time_ms == 0.15
    assert report.execution_time_ms == 1.35
    assert report.shared_hit_blocks == 12
    assert report.shared_read_blocks == 5
    assert len(report.warnings) == 1
    assert "Sequential Scan (Seq Scan) detected on 'catalog_items'" in report.warnings[0]


def test_query_plan_parser_detects_bitmap_index_scan() -> None:
    """Verify parse_postgres_plan correctly resolves GIN / Bitmap Index Scan execution trees."""
    sample_gin_plan = [
        {
            "Plan": {
                "Node Type": "Bitmap Heap Scan",
                "Parallel Aware": False,
                "Async Capable": False,
                "Relation Name": "catalog_items",
                "Alias": "catalog_items",
                "Startup Cost": 8.05,
                "Total Cost": 12.35,
                "Plan Rows": 10,
                "Plan Width": 340,
                "Actual Startup Time": 0.045,
                "Actual Total Time": 0.095,
                "Actual Rows": 8,
                "Actual Loops": 1,
                "Shared Hit Blocks": 4,
                "Shared Read Blocks": 0,
                "Plans": [
                    {
                        "Node Type": "Bitmap Index Scan",
                        "Parent Relationship": "Outer",
                        "Parallel Aware": False,
                        "Async Capable": False,
                        "Index Name": "ix_catalog_metadata_gin",
                        "Startup Cost": 0.0,
                        "Total Cost": 8.05,
                        "Plan Rows": 10,
                        "Plan Width": 0,
                        "Actual Startup Time": 0.035,
                        "Actual Total Time": 0.035,
                        "Actual Rows": 8,
                        "Actual Loops": 1,
                        "Shared Hit Blocks": 2,
                        "Shared Read Blocks": 0,
                    }
                ],
            },
            "Planning Time": 0.08,
            "Execution Time": 0.12,
        }
    ]

    report = QueryPlanService.parse_postgres_plan(sample_gin_plan)
    assert report.dialect == "postgresql"
    assert "Bitmap Index Scan" in report.scan_types
    assert "Bitmap Heap Scan" in report.scan_types
    assert report.total_cost == 12.35
    assert report.planning_time_ms == 0.08
    assert report.execution_time_ms == 0.12
    # Combined shared hits from root and sub-plan: 4 + 2 = 6
    assert report.shared_hit_blocks == 6
    assert report.shared_read_blocks == 0
    # Zero Seq Scan warnings
    assert len(report.warnings) == 0


def test_validate_read_only_query_blocks_all_mutating_statements() -> None:
    """Verify strict read-only execution guard blocks all data and schema mutation attacks."""
    dangerous_queries = [
        "INSERT INTO catalog_items (sku, name) VALUES ('hacked', 'pwned')",
        "UPDATE catalog_items SET price = 0.0",
        "DELETE FROM catalog_items WHERE id > 0",
        "DROP TABLE catalog_items",
        "ALTER TABLE catalog_items DROP COLUMN price",
        "TRUNCATE TABLE catalog_items",
        "CREATE TABLE backdoor (id int)",
        "GRANT ALL PRIVILEGES ON ALL TABLES TO PUBLIC",
        "REVOKE SELECT ON catalog_items FROM PUBLIC",
        "SELECT * FROM catalog_items; DROP TABLE users;",
        "-- comment\nDELETE FROM catalog_items",
        "/* multi-line comment */ DROP TABLE users",
        "",
        "   ",
    ]

    for q in dangerous_queries:
        with pytest.raises(UnsafeQueryExecutionException):
            QueryPlanService.validate_read_only_query(q)


def test_validate_read_only_query_permits_safe_queries() -> None:
    """Verify safe SELECT queries pass validation."""
    safe_queries = [
        "SELECT * FROM catalog_items WHERE price < 50.0",
        "SELECT id, sku, name FROM catalog_items ORDER BY id DESC LIMIT 10",
        "WITH cheap_items AS (SELECT * FROM catalog_items WHERE price < 20) SELECT * FROM cheap_items",
        "/* read probe */ SELECT 1 AS probe",
    ]

    for q in safe_queries:
        # Should not raise
        QueryPlanService.validate_read_only_query(q)


@pytest.mark.asyncio
async def test_live_sqlite_query_plan_analysis() -> None:
    """Verify live query plan execution against active database session."""
    async with async_session_factory() as session:
        report = await QueryPlanService.analyze_query_execution_plan(
            session=session,
            sql_query="SELECT * FROM catalog_items WHERE sku = :sku",
            params={"sku": "NON_EXISTENT_SKU"},
        )
        assert isinstance(report, QueryPlanReport)
        assert report.dialect in ("sqlite", "postgresql")
        assert len(report.nodes) >= 1


def test_api_create_catalog_item(client: TestClient) -> None:
    """Verify POST /catalog/items persists item and returns HTTP 201."""
    unique_sku = f"SKU-API-{datetime.now(UTC).timestamp()}"
    payload: dict[str, Any] = {
        "sku": unique_sku,
        "name": "Smart Fitness Watch",
        "category": "wearables",
        "price": 149.50,
        "barcode": f"BAR-API-{datetime.now(UTC).timestamp()}",
        "metadata_json": {"sensors": ["heart_rate", "gps", "sp02"], "waterproof": True},
        "tags": ["wearables", "fitness", "bluetooth"],
    }

    response = client.post("/catalog/items", json=payload)
    assert response.status_code == 201
    data = response.json()
    assert data["sku"] == unique_sku
    assert data["category"] == "wearables"
    assert data["price"] == 149.50
    assert "fitness" in data["tags"]
    assert data["metadata_json"]["waterproof"] is True


def test_api_search_catalog_by_tags(client: TestClient) -> None:
    """Verify GET /catalog/search/tags retrieves items and includes plan diagnostic."""
    response = client.get("/catalog/search/tags?tag=fitness")
    assert response.status_code == 200
    data = response.json()
    assert data["tag"] == "fitness"
    assert "items" in data
    assert "plan_report" in data
    assert data["plan_report"]["dialect"] in ("sqlite", "postgresql")


def test_api_explain_diagnostics_success(client: TestClient) -> None:
    """Verify POST /catalog/diagnostics/explain returns parsed QueryPlanReport."""
    payload = {
        "query": "SELECT id, sku, name FROM catalog_items WHERE category = :cat",
        "params": {"cat": "wearables"},
    }

    response = client.post("/catalog/diagnostics/explain", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert "scan_types" in data
    assert "total_cost" in data
    assert "nodes" in data
    assert data["dialect"] in ("sqlite", "postgresql")


def test_api_explain_diagnostics_blocks_mutations(client: TestClient) -> None:
    """Verify POST /catalog/diagnostics/explain rejects mutating queries with HTTP 400."""
    payload = {
        "query": "DELETE FROM catalog_items WHERE id > 0",
        "params": {},
    }

    response = client.post("/catalog/diagnostics/explain", json=payload)
    assert response.status_code == 400
    err_body = response.json()
    assert err_body["error"]["code"] == "UNSAFE_DIAGNOSTIC_QUERY"
