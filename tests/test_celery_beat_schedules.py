"""Comprehensive test suite for Day 52: Periodic Task Scheduling & Cron Pipelines with Celery Beat."""

from __future__ import annotations

from typing import Any

import pytest
from celery.schedules import crontab
from fastapi.testclient import TestClient

from app.core.celery_app import celery_app
from app.core.exceptions import EntityNotFoundException
from app.services.schedule_service import ScheduleService
from app.tasks.scheduled_tasks import (
    nightly_reconciliation_audit,
    prune_expired_sessions_and_tokens,
    system_health_heartbeat,
)


def test_beat_schedule_registry_integrity() -> None:
    """Verify that all 3 periodic tasks are configured in celery_app.conf.beat_schedule."""
    beat_schedule: dict[str, Any] = getattr(celery_app.conf, "beat_schedule", {})
    assert len(beat_schedule) == 3

    # Task 1: Nightly Financial Audit
    assert "nightly-reconciliation-audit" in beat_schedule
    audit_cfg = beat_schedule["nightly-reconciliation-audit"]
    assert audit_cfg["task"] == "app.tasks.scheduled_tasks.nightly_reconciliation_audit"
    assert isinstance(audit_cfg["schedule"], crontab)

    # Task 2: Hourly Session Pruning
    assert "prune-expired-sessions-and-tokens" in beat_schedule
    prune_cfg = beat_schedule["prune-expired-sessions-and-tokens"]
    assert prune_cfg["task"] == "app.tasks.scheduled_tasks.prune_expired_sessions_and_tokens"
    assert isinstance(prune_cfg["schedule"], crontab)

    # Task 3: Heartbeat interval
    assert "system-health-heartbeat" in beat_schedule
    heartbeat_cfg = beat_schedule["system-health-heartbeat"]
    assert heartbeat_cfg["task"] == "app.tasks.scheduled_tasks.system_health_heartbeat"
    assert heartbeat_cfg["schedule"] == 60.0

    # Persistence shelf filename check
    assert getattr(celery_app.conf, "beat_schedule_filename", None) == "celerybeat-schedule"


def test_crontab_schedule_evaluation() -> None:
    """Verify crontab matching behavior for midnight and hourly schedules."""
    midnight_cron = crontab(hour=0, minute=0)
    hourly_cron = crontab(minute=0)

    # Verify midnight crontab evaluates strictly at hour 0, minute 0
    assert 0 in midnight_cron.hour
    assert 0 in midnight_cron.minute
    assert len(midnight_cron.hour) == 1
    assert len(midnight_cron.minute) == 1

    # Verify hourly crontab evaluates at minute 0 across all 24 hours
    assert 0 in hourly_cron.minute
    assert len(hourly_cron.minute) == 1
    assert len(hourly_cron.hour) == 24  # every hour of the day


def test_nightly_reconciliation_audit_task_direct_execution() -> None:
    """Execute nightly_reconciliation_audit task and verify output structure and integrity."""
    result = nightly_reconciliation_audit.apply()

    assert result.status == "SUCCESS"
    data = result.result
    assert data["status"] == "COMPLETED"
    assert data["task_name"] == "nightly_reconciliation_audit"
    assert data["audited_accounts"] == 1250
    assert data["total_debits"] == 4_500_000.00
    assert data["total_credits"] == 4_500_000.00
    assert data["discrepancies"] == 0
    assert data["balanced"] is True
    assert "checksum_sha256" in data
    assert len(data["checksum_sha256"]) == 64


def test_prune_expired_sessions_task_direct_execution() -> None:
    """Execute prune_expired_sessions_and_tokens task and verify eviction metrics."""
    result = prune_expired_sessions_and_tokens.apply()

    assert result.status == "SUCCESS"
    data = result.result
    assert data["status"] == "COMPLETED"
    assert data["task_name"] == "prune_expired_sessions_and_tokens"
    assert data["scanned_records"] == 340
    assert data["evicted_count"] == 42
    assert "pruned_at" in data


def test_system_health_heartbeat_task_direct_execution() -> None:
    """Execute system_health_heartbeat task and verify health probe metadata."""
    result = system_health_heartbeat.apply()

    assert result.status == "SUCCESS"
    data = result.result
    assert data["status"] == "HEALTHY"
    assert data["task_name"] == "system_health_heartbeat"
    assert data["database"] == "OK"
    assert data["redis"] == "OK"
    assert "latency_ms" in data


def test_schedule_service_get_active_schedules() -> None:
    """Test ScheduleService returns active schedules matching registered Celery Beat entries."""
    schedules_dto = ScheduleService.get_active_schedules()

    assert schedules_dto.total_schedules == 3
    names = {s.name for s in schedules_dto.schedules}
    assert "nightly-reconciliation-audit" in names
    assert "prune-expired-sessions-and-tokens" in names
    assert "system-health-heartbeat" in names


def test_schedule_service_manual_trigger_success() -> None:
    """Test ScheduleService manually dispatches a valid registered periodic task."""
    task_id = ScheduleService.trigger_scheduled_task_manually("nightly-reconciliation-audit")
    assert isinstance(task_id, str)
    assert len(task_id) > 0


def test_schedule_service_manual_trigger_unknown_task() -> None:
    """Test ScheduleService raises EntityNotFoundException for unregistered task names."""
    with pytest.raises(EntityNotFoundException) as exc_info:
        ScheduleService.trigger_scheduled_task_manually("unknown-ghost-cron-task")

    assert exc_info.value.code == "SCHEDULED_TASK_NOT_FOUND"


def test_api_get_schedules_endpoint(client: TestClient) -> None:
    """Verify HTTP GET /schedules returns list of configured periodic tasks."""
    response = client.get("/schedules")
    assert response.status_code == 200

    data = response.json()
    assert data["total_schedules"] == 3
    assert len(data["schedules"]) == 3

    schedule_names = [s["name"] for s in data["schedules"]]
    assert "nightly-reconciliation-audit" in schedule_names
    assert "prune-expired-sessions-and-tokens" in schedule_names
    assert "system-health-heartbeat" in schedule_names


def test_api_manual_trigger_endpoint_success(client: TestClient) -> None:
    """Verify HTTP POST /schedules/trigger/{task_name} dispatches job and returns 202."""
    response = client.post("/schedules/trigger/nightly-reconciliation-audit")
    assert response.status_code == 202

    data = response.json()
    assert data["status"] == "PENDING"
    assert data["task_name"] == "nightly-reconciliation-audit"
    assert "task_id" in data
    assert len(data["task_id"]) > 0


def test_api_manual_trigger_endpoint_not_found(client: TestClient) -> None:
    """Verify HTTP POST /schedules/trigger/{task_name} returns 404 for unregistered task."""
    response = client.post("/schedules/trigger/non-existent-cron-pipeline")
    assert response.status_code == 404

    data = response.json()
    assert data["error"]["code"] == "SCHEDULED_TASK_NOT_FOUND"
