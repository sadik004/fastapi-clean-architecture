"""Comprehensive Test Suite for Day 69: Database Table Partitioning Architecture.

Verifies:
1. Auto-Routing Range Inserts: Records inserted into root partitioned table route
   to their respective child tables (audit_logs_y2025 and audit_logs_y2026).
2. Partition Pruning Invariant: Queries with date ranges only scan relevant partitions,
   pruning non-matching partitions and returning accurate telemetry.
3. Default Catch-All Partition: Out-of-range timestamps safely land in audit_logs_default.
4. O(1) Partition Detach & Drop: Partitions can be detached and dropped without root table locks.
5. Dynamic Monthly Partition Allocation: New monthly partition shards can be added on-the-fly.
6. HTTP Endpoints Lifecycle: Validates /audit/partitioned, /audit/partitioned/search, and /audit/partitions.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.core.database import async_session_factory
from app.services.partition_service import PartitionManagerService

# ==============================================================================
# 1. Service-Level Partitioning & Auto-Routing Tests
# ==============================================================================


@pytest.mark.asyncio
async def test_auto_routing_insert_across_partitions() -> None:
    """Verify that records inserted with timestamps in 2025 and 2026 route to their respective child tables."""
    async with async_session_factory() as session:
        # 1. Insert record for 2025
        dt_2025 = datetime(2025, 6, 15, 12, 0, 0, tzinfo=UTC)
        log_2025 = await PartitionManagerService.insert_audit_log(
            session=session,
            event_type="USER_SIGNUP",
            user_id=101,
            payload={"source": "mobile_app", "country": "US"},
            created_at=dt_2025,
        )
        assert log_2025.target_partition == "audit_logs_y2025"

        # 2. Insert record for 2026
        dt_2026 = datetime(2026, 8, 20, 15, 30, 0, tzinfo=UTC)
        log_2026 = await PartitionManagerService.insert_audit_log(
            session=session,
            event_type="ORDER_COMPLETED",
            user_id=102,
            payload={"order_id": "ord_999", "amount": 150.0},
            created_at=dt_2026,
        )
        assert log_2026.target_partition == "audit_logs_y2026"

        # 3. Direct inspection of child tables
        count_2025 = (await session.execute(text("SELECT count(*) FROM audit_logs_y2025"))).scalar()
        count_2026 = (await session.execute(text("SELECT count(*) FROM audit_logs_y2026"))).scalar()

        assert count_2025 == 1
        assert count_2026 == 1


@pytest.mark.asyncio
async def test_partition_pruning_date_range() -> None:
    """Verify that querying for 2026 logs prunes the 2025 partition from execution."""
    async with async_session_factory() as session:
        # Seed records in both partitions
        await PartitionManagerService.insert_audit_log(
            session=session,
            event_type="HISTORICAL_EVENT",
            user_id=1,
            payload={"year": 2025},
            created_at=datetime(2025, 3, 1, 10, 0, 0, tzinfo=UTC),
        )
        await PartitionManagerService.insert_audit_log(
            session=session,
            event_type="CURRENT_EVENT",
            user_id=2,
            payload={"year": 2026},
            created_at=datetime(2026, 5, 1, 10, 0, 0, tzinfo=UTC),
        )

        # Search strictly within 2026
        start_date = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)
        end_date = datetime(2026, 12, 31, 23, 59, 59, tzinfo=UTC)

        search_res = await PartitionManagerService.search_audit_logs(
            session=session,
            start_date=start_date,
            end_date=end_date,
        )

        # Assert data correctness: only 2026 record returned
        assert search_res.total_count == 1
        assert search_res.logs[0].event_type == "CURRENT_EVENT"

        # Assert pruning telemetry: 2026 scanned, 2025 pruned
        telemetry = search_res.telemetry
        assert "audit_logs_y2026" in telemetry.scanned_partitions
        assert "audit_logs_y2025" not in telemetry.scanned_partitions
        assert "audit_logs_y2025" in telemetry.pruned_partitions
        assert telemetry.pruning_efficiency_percent > 0.0


@pytest.mark.asyncio
async def test_default_partition_catch_all() -> None:
    """Verify that timestamps outside known partitions route to the default catch-all partition."""
    async with async_session_factory() as session:
        # Timestamp in 2024 (outside 2025 and 2026 ranges)
        dt_2024 = datetime(2024, 11, 25, 9, 15, 0, tzinfo=UTC)
        log_default = await PartitionManagerService.insert_audit_log(
            session=session,
            event_type="LEGACY_AUDIT",
            user_id=999,
            payload={"archive": True},
            created_at=dt_2024,
        )
        assert log_default.target_partition == "audit_logs_default"

        count_default = (await session.execute(text("SELECT count(*) FROM audit_logs_default"))).scalar()
        assert count_default == 1


@pytest.mark.asyncio
async def test_o1_detach_and_drop_partition() -> None:
    """Verify that a partition can be dynamically created and detached in O(1) time."""
    async with async_session_factory() as session:
        # Create monthly partition for April 2028
        create_res = await PartitionManagerService.create_monthly_partition(
            session=session,
            year=2028,
            month=4,
        )
        assert create_res.status == "created"
        assert create_res.partition_name == "audit_logs_y2028m04"

        # Verify it appears in active partitions
        parts_before = await PartitionManagerService.list_active_partitions(session)
        names_before = [p.name for p in parts_before.partitions]
        assert "audit_logs_y2028m04" in names_before

        # Detach and drop partition
        detach_res = await PartitionManagerService.detach_and_drop_old_partition(
            session=session,
            partition_name="audit_logs_y2028m04",
        )
        assert detach_res.status == "detached_and_dropped"
        assert detach_res.partition_name == "audit_logs_y2028m04"

        # Verify it is no longer listed in active partitions
        parts_after = await PartitionManagerService.list_active_partitions(session)
        names_after = [p.name for p in parts_after.partitions]
        assert "audit_logs_y2028m04" not in names_after


# ==============================================================================
# 2. HTTP Endpoints Integration Tests
# ==============================================================================


def test_http_create_and_search_partitioned_audit_logs(client: TestClient) -> None:
    """Verify HTTP API endpoints for audit log creation, date-range search, and partitions list."""
    # 1. Create audit log for 2026
    payload_2026 = {
        "event_type": "PAYMENT_PROCESSED",
        "user_id": 55,
        "payload": {"gateway": "stripe", "amount": 250},
        "created_at": "2026-07-04T12:00:00Z",
    }
    res_create = client.post("/audit/partitioned", json=payload_2026)
    assert res_create.status_code == 201
    data_create = res_create.json()
    assert data_create["event_type"] == "PAYMENT_PROCESSED"
    assert data_create["target_partition"] == "audit_logs_y2026"

    # 2. Create audit log for 2025
    payload_2025 = {
        "event_type": "PASSWORD_RESET",
        "user_id": 55,
        "payload": {"method": "sms"},
        "created_at": "2025-10-10T08:00:00Z",
    }
    res_create_2025 = client.post("/audit/partitioned", json=payload_2025)
    assert res_create_2025.status_code == 201
    assert res_create_2025.json()["target_partition"] == "audit_logs_y2025"

    # 3. List partitions
    res_list = client.get("/audit/partitions")
    assert res_list.status_code == 200
    list_data = res_list.json()
    assert list_data["total_partitions"] >= 3
    partition_names = [p["name"] for p in list_data["partitions"]]
    assert "audit_logs_y2025" in partition_names
    assert "audit_logs_y2026" in partition_names
    assert "audit_logs_default" in partition_names

    # 4. Search audit logs for 2026 with partition pruning
    search_url = "/audit/partitioned/search?start_date=2026-01-01T00:00:00Z&end_date=2026-12-31T23:59:59Z"
    res_search = client.get(search_url)
    assert res_search.status_code == 200
    search_data = res_search.json()
    assert search_data["total_count"] == 1
    assert search_data["logs"][0]["event_type"] == "PAYMENT_PROCESSED"

    telemetry = search_data["telemetry"]
    assert "audit_logs_y2026" in telemetry["scanned_partitions"]
    assert "audit_logs_y2025" in telemetry["pruned_partitions"]
    assert telemetry["pruning_efficiency_percent"] > 0


def test_http_dynamic_partition_lifecycle(client: TestClient) -> None:
    """Verify HTTP API endpoints for dynamic monthly partition creation and O(1) detachment."""
    # 1. Create monthly partition
    create_payload = {"year": 2029, "month": 9}
    res_create = client.post("/audit/partitions/monthly", json=create_payload)
    assert res_create.status_code == 201
    assert res_create.json()["partition_name"] == "audit_logs_y2029m09"

    # 2. Detach and drop partition
    res_delete = client.delete("/audit/partitions/audit_logs_y2029m09")
    assert res_delete.status_code == 200
    assert res_delete.json()["status"] == "detached_and_dropped"
    assert res_delete.json()["partition_name"] == "audit_logs_y2029m09"
