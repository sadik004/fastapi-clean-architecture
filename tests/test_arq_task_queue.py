"""Comprehensive test suite for Day 53: Asyncio-Native Task Queues with ARQ & Coroutine Workers."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from arq.jobs import JobStatus
from fastapi.testclient import TestClient

from app.core.arq_app import WorkerSettings, shutdown, startup
from app.schemas.arq import ArqJobStatusResponse
from app.services.arq_service import ArqService
from app.tasks.arq_tasks import broadcast_push_notification, sync_webhook_notification


@pytest.mark.asyncio
async def test_sync_webhook_notification_coroutine_success() -> None:
    """Test sync_webhook_notification executes cleanly without client context."""
    payload = {"event": "order.completed", "amount": 150.00}
    result = await sync_webhook_notification(
        ctx={},
        webhook_url="https://hooks.example.com/test",
        payload=payload,
    )

    assert result["status"] == "DELIVERED"
    assert result["webhook_url"] == "https://hooks.example.com/test"
    assert result["status_code"] == 200
    assert result["payload_keys"] == ["amount", "event"]
    assert "attempted_at" in result


@pytest.mark.asyncio
async def test_sync_webhook_notification_simulated_error() -> None:
    """Test sync_webhook_notification raises error when simulate_error is requested."""
    with pytest.raises(ConnectionError, match="Simulated webhook failure"):
        await sync_webhook_notification(
            ctx={},
            webhook_url="https://hooks.example.com/error",
            payload={"simulate_error": True},
        )


@pytest.mark.asyncio
async def test_sync_webhook_with_mock_http_client() -> None:
    """Test sync_webhook_notification uses shared http_client from worker context."""
    mock_client = AsyncMock()
    mock_response = MagicMock(status_code=202)
    mock_client.post.return_value = mock_response

    result = await sync_webhook_notification(
        ctx={"http_client": mock_client},
        webhook_url="https://api.thirdparty.com/webhook",
        payload={"invoice_id": "INV-99"},
    )

    assert mock_client.post.await_count == 1
    assert result["status_code"] == 202
    assert result["status"] == "DELIVERED"


@pytest.mark.asyncio
async def test_broadcast_push_notification_coroutine() -> None:
    """Test broadcast_push_notification produces structured metrics and sha256 digest."""
    user_ids = [101, 102, 103, 104]
    message = "Flash sale starts in 10 minutes!"

    result = await broadcast_push_notification(
        ctx={},
        user_ids=user_ids,
        message=message,
    )

    assert result["status"] == "BROADCAST_COMPLETED"
    assert result["total_recipients"] == 4
    assert result["delivered_count"] == 4
    assert result["user_ids"] == [101, 102, 103, 104]
    assert len(result["message_digest"]) == 64
    assert "broadcast_at" in result


@pytest.mark.asyncio
async def test_arq_worker_settings_and_lifespan() -> None:
    """Verify ARQ WorkerSettings definitions and startup/shutdown lifespan hooks."""
    assert WorkerSettings.max_jobs == 100
    assert WorkerSettings.job_timeout == 60
    assert WorkerSettings.keep_result == 3600
    assert sync_webhook_notification in WorkerSettings.functions
    assert broadcast_push_notification in WorkerSettings.functions

    ctx: dict[str, Any] = {}
    await startup(ctx)
    assert "http_client" in ctx
    client = ctx["http_client"]

    await shutdown(ctx)
    assert client.is_closed


@pytest.mark.asyncio
async def test_arq_service_enqueue_methods() -> None:
    """Test ArqService enqueuing webhook and broadcast jobs using pool override."""
    mock_pool = AsyncMock()
    mock_job = MagicMock(job_id="mock-job-xyz-789")
    mock_pool.enqueue_job.return_value = mock_job

    try:
        ArqService.set_pool_override(mock_pool)

        wh_job_id = await ArqService.enqueue_webhook_task(
            webhook_url="https://example.com/wh",
            payload={"msg": "ping"},
        )
        assert wh_job_id == "mock-job-xyz-789"
        mock_pool.enqueue_job.assert_awaited_with(
            "sync_webhook_notification",
            "https://example.com/wh",
            {"msg": "ping"},
        )

        bc_job_id = await ArqService.enqueue_broadcast_task(
            user_ids=[1, 2, 3],
            message="Alert message",
        )
        assert bc_job_id == "mock-job-xyz-789"
        mock_pool.enqueue_job.assert_awaited_with(
            "broadcast_push_notification",
            [1, 2, 3],
            "Alert message",
        )
    finally:
        ArqService.set_pool_override(None)


@pytest.mark.asyncio
async def test_arq_service_get_job_status() -> None:
    """Test ArqService.get_job_status correctly maps Job attributes into DTO."""
    mock_pool = AsyncMock()
    now_dt = datetime.now(UTC)

    mock_job = AsyncMock()
    mock_job.status.return_value = JobStatus.complete
    mock_info = MagicMock(enqueue_time=now_dt)
    mock_job.info.return_value = mock_info
    mock_result_info = MagicMock(result={"status": "DELIVERED"}, success=True)
    mock_job.result_info.return_value = mock_result_info

    try:
        ArqService.set_pool_override(mock_pool)
        with patch("app.services.arq_service.Job", return_value=mock_job):
            status_resp = await ArqService.get_job_status("job-complete-123")

        assert status_resp.job_id == "job-complete-123"
        assert status_resp.status == "complete"
        assert status_resp.success is True
        assert status_resp.result == {"status": "DELIVERED"}
        assert status_resp.enqueue_time == now_dt.isoformat()
    finally:
        ArqService.set_pool_override(None)


def test_api_enqueue_webhook_job_endpoint(client: TestClient) -> None:
    """Test POST /arq/jobs/webhook returns HTTP 202 Accepted with job_id."""
    with patch(
        "app.services.arq_service.ArqService.enqueue_webhook_task",
        new=AsyncMock(return_value="wh-job-001"),
    ):
        response = client.post(
            "/arq/jobs/webhook",
            json={
                "webhook_url": "https://service.partner.com/notify",
                "payload": {"event": "payment.authorized", "id": 99},
            },
        )

    assert response.status_code == 202
    data = response.json()
    assert data["job_id"] == "wh-job-001"
    assert data["job_name"] == "sync_webhook_notification"
    assert data["status"] == "QUEUED"


def test_api_enqueue_broadcast_job_endpoint(client: TestClient) -> None:
    """Test POST /arq/jobs/broadcast returns HTTP 202 Accepted with job_id."""
    with patch(
        "app.services.arq_service.ArqService.enqueue_broadcast_task",
        new=AsyncMock(return_value="bc-job-002"),
    ):
        response = client.post(
            "/arq/jobs/broadcast",
            json={
                "user_ids": [10, 20, 30],
                "message": "Maintenance notification.",
            },
        )

    assert response.status_code == 202
    data = response.json()
    assert data["job_id"] == "bc-job-002"
    assert data["job_name"] == "broadcast_push_notification"
    assert data["status"] == "QUEUED"


def test_api_get_job_status_endpoint(client: TestClient) -> None:
    """Test GET /arq/jobs/{job_id} returns job execution details."""
    mock_status = ArqJobStatusResponse(
        job_id="test-job-999",
        status="complete",
        result={"status": "DELIVERED", "status_code": 200},
        enqueue_time="2026-09-09T22:00:00Z",
        success=True,
    )
    with patch(
        "app.services.arq_service.ArqService.get_job_status",
        new=AsyncMock(return_value=mock_status),
    ):
        response = client.get("/arq/jobs/test-job-999")

    assert response.status_code == 200
    data = response.json()
    assert data["job_id"] == "test-job-999"
    assert data["status"] == "complete"
    assert data["success"] is True
    assert data["result"]["status_code"] == 200
