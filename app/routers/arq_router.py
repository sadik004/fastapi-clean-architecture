"""Router exposing ARQ asyncio-native background job dispatch and status inspection endpoints."""

from __future__ import annotations

from fastapi import APIRouter, status

from app.schemas.arq import (
    ArqJobEnqueueResponse,
    ArqJobStatusResponse,
    PushNotificationJobRequest,
    WebhookJobRequest,
)
from app.services.arq_service import ArqService

router = APIRouter(prefix="/arq", tags=["ARQ Background Queue"])


@router.post(
    "/jobs/webhook",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=ArqJobEnqueueResponse,
    summary="Enqueue an asynchronous outbound webhook notification",
)
async def enqueue_webhook_job(request: WebhookJobRequest) -> ArqJobEnqueueResponse:
    """Queue a non-blocking HTTP webhook notification task into the ARQ Redis queue.

    Returns HTTP 202 Accepted with a unique job_id for non-blocking asynchronous execution.
    """
    job_id = await ArqService.enqueue_webhook_task(
        webhook_url=request.webhook_url,
        payload=request.payload,
    )
    return ArqJobEnqueueResponse(
        job_id=job_id,
        job_name="sync_webhook_notification",
        status="QUEUED",
        message="Outbound webhook job enqueued successfully.",
    )


@router.post(
    "/jobs/broadcast",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=ArqJobEnqueueResponse,
    summary="Enqueue a high-volume push notification broadcast",
)
async def enqueue_broadcast_job(request: PushNotificationJobRequest) -> ArqJobEnqueueResponse:
    """Queue an asynchronous push notification broadcast fan-out into the ARQ Redis queue.

    Returns HTTP 202 Accepted immediately.
    """
    job_id = await ArqService.enqueue_broadcast_task(
        user_ids=request.user_ids,
        message=request.message,
    )
    return ArqJobEnqueueResponse(
        job_id=job_id,
        job_name="broadcast_push_notification",
        status="QUEUED",
        message=f"Broadcast notification job enqueued for {len(request.user_ids)} recipients.",
    )


@router.get(
    "/jobs/{job_id}",
    response_model=ArqJobStatusResponse,
    summary="Poll execution status and result of an ARQ background job",
)
async def get_job_status(job_id: str) -> ArqJobStatusResponse:
    """Retrieve the current execution status, timings, and result payload of an ARQ job."""
    return await ArqService.get_job_status(job_id=job_id)
