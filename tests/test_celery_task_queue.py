"""Tests for Celery distributed task queue and background reporting services."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from celery.exceptions import Retry
from fastapi.testclient import TestClient

from app.core.celery_app import celery_app
from app.main import app
from app.services.task_service import TaskService
from app.tasks.report_tasks import generate_pdf_report, send_transactional_email


def test_celery_configuration_hardening() -> None:
    """Verify Celery application hardening parameters in core configuration."""
    conf = celery_app.conf
    assert conf.task_serializer == "json"
    assert conf.result_serializer == "json"
    assert conf.accept_content == ["json"]
    assert conf.timezone == "UTC"
    assert conf.enable_utc is True
    assert conf.task_track_started is True
    assert conf.task_time_limit == 300
    assert conf.task_soft_time_limit == 240
    assert conf.worker_prefetch_multiplier == 1
    assert conf.task_acks_late is True


def test_task_dispatch_and_execution() -> None:
    """Verify direct dispatch of PDF generation task executes and returns expected result structure."""
    result = generate_pdf_report.delay(user_id=101, report_type="financial_annual")
    assert result.successful() is True
    assert result.state == "SUCCESS"

    payload = result.result
    assert isinstance(payload, dict)
    assert payload["status"] == "COMPLETED"
    assert payload["user_id"] == 101
    assert payload["report_type"] == "financial_annual"
    assert "report_101_financial_annual" in payload["filename"]
    assert len(payload["checksum_sha256"]) == 64
    assert payload["size_bytes"] > 0
    assert "generated_at" in payload
    assert payload["execution_time_seconds"] >= 0


def test_service_task_dispatch_and_status() -> None:
    """Verify TaskService dispatches and correctly inspects task execution status."""
    task_id = TaskService.dispatch_report_generation(user_id=42, report_type="audit_log")
    assert isinstance(task_id, str)
    assert len(task_id) > 0

    status_data = TaskService.get_task_status(task_id=task_id)
    assert status_data["task_id"] == task_id
    assert status_data["status"] == "SUCCESS"
    assert status_data["error"] is None
    assert status_data["result"] is not None
    assert status_data["result"]["user_id"] == 42
    assert status_data["result"]["report_type"] == "audit_log"


def test_http_endpoint_dispatch_acceptance() -> None:
    """Verify POST /tasks/reports/generate returns HTTP 202 Accepted with valid task_id."""
    with TestClient(app) as client:
        response = client.post(
            "/tasks/reports/generate",
            json={"user_id": 99, "report_type": "security_audit"},
        )
        assert response.status_code == 202
        body = response.json()
        assert "task_id" in body
        assert body["status"] == "PENDING"
        assert "dispatched successfully" in body["message"]

        # Poll status for the dispatched task
        task_id = body["task_id"]
        status_response = client.get(f"/tasks/reports/status/{task_id}")
        assert status_response.status_code == 200
        status_body = status_response.json()
        assert status_body["task_id"] == task_id
        assert status_body["status"] == "SUCCESS"
        assert status_body["result"]["user_id"] == 99
        assert status_body["result"]["report_type"] == "security_audit"


def test_http_endpoint_validation_error() -> None:
    """Verify validation error handling on invalid report generation request."""
    with TestClient(app) as client:
        # user_id <= 0
        response = client.post(
            "/tasks/reports/generate",
            json={"user_id": -1, "report_type": "invalid"},
        )
        assert response.status_code == 422


def test_transactional_email_dispatch() -> None:
    """Verify background transactional email task dispatches and succeeds."""
    result = send_transactional_email.delay(
        recipient="user@enterprise.com",
        subject="Monthly Account Statement",
        template="monthly_statement",
        context={"month": "October", "year": 2026},
    )
    assert result.successful() is True
    payload = result.result
    assert payload["status"] == "SENT"
    assert payload["recipient"] == "user@enterprise.com"
    assert payload["subject"] == "Monthly Account Statement"
    assert payload["attempt"] == 1


def test_task_retry_mechanism_on_simulated_failure() -> None:
    """Verify Celery task retry mechanism on transient connection error."""
    # When simulate_error is true, retry is raised
    with pytest.raises(Retry):
        send_transactional_email.apply(
            args=[
                "failure@enterprise.com",
                "Failure Test",
                "alert",
                {"simulate_error": True},
            ],
            throw=True,
        )


def test_task_service_failure_status_handling() -> None:
    """Verify TaskService handles failed task states gracefully."""
    with patch("app.services.task_service.AsyncResult") as mock_async_result:
        mock_result_instance = mock_async_result.return_value
        mock_result_instance.state = "FAILURE"
        mock_result_instance.result = RuntimeError("Worker memory overflow error")

        status_data = TaskService.get_task_status("mock-failed-task-id")
        assert status_data["task_id"] == "mock-failed-task-id"
        assert status_data["status"] == "FAILURE"
        assert status_data["result"] is None
        assert "Worker memory overflow error" in str(status_data["error"])


def test_task_service_pending_status_handling() -> None:
    """Verify TaskService handles pending/in-progress task states gracefully."""
    with patch("app.services.task_service.AsyncResult") as mock_async_result:
        mock_result_instance = mock_async_result.return_value
        mock_result_instance.state = "PENDING"
        mock_result_instance.result = None

        status_data = TaskService.get_task_status("mock-pending-task-id")
        assert status_data["task_id"] == "mock-pending-task-id"
        assert status_data["status"] == "PENDING"
        assert status_data["result"] is None
        assert status_data["error"] is None
