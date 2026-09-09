"""FastAPI Router exposing Dead Letter Queue (DLQ) Management & Redrive Endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Query, status

from app.schemas.dlq import (
    DLQMessageListResponse,
    DLQPurgeResponse,
    DLQRedriveRequest,
    DLQRedriveResponse,
)
from app.services.dlq_service import DLQService

router = APIRouter(prefix="/dlq", tags=["Dead Letter Queue (DLQ) & Resilience"])


@router.get(
    "/messages",
    response_model=DLQMessageListResponse,
    summary="List quarantined messages in the Dead Letter Queue",
)
async def list_quarantined_messages(
    queue_or_topic: str | None = Query(
        default=None,
        description="Filter by original queue or topic name",
    ),
) -> DLQMessageListResponse:
    """Retrieve active dead-letter messages with failure diagnostics and error stack traces."""
    envelopes = DLQService.get_quarantined_messages(queue_or_topic=queue_or_topic)
    return DLQMessageListResponse(
        total_quarantined=len(envelopes),
        messages=envelopes,
    )


@router.post(
    "/redrive",
    status_code=status.HTTP_200_OK,
    response_model=DLQRedriveResponse,
    summary="Redrive quarantined messages back into the primary processing pipeline",
)
async def redrive_messages(request: DLQRedriveRequest) -> DLQRedriveResponse:
    """Re-inject dead-lettered messages back into their original queues or topics for reprocessing."""
    count = await DLQService.redrive_messages(
        queue_or_topic=request.queue_or_topic,
        limit=request.limit,
    )
    destination_desc = request.queue_or_topic if request.queue_or_topic else "all origin queues/topics"
    return DLQRedriveResponse(
        redriven_count=count,
        target_destination=destination_desc,
        message=f"Successfully redrove {count} messages back to {destination_desc}",
    )


@router.post(
    "/purge",
    status_code=status.HTTP_200_OK,
    response_model=DLQPurgeResponse,
    summary="Purge quarantined messages from the DLQ",
)
async def purge_messages(
    queue_or_topic: str | None = Query(
        default=None,
        description="Purge only messages matching specific queue or topic",
    ),
) -> DLQPurgeResponse:
    """Permanently discard quarantined messages from the dead letter buffer."""
    count = DLQService.purge_dlq(queue_or_topic=queue_or_topic)
    return DLQPurgeResponse(
        purged_count=count,
        message=f"Successfully purged {count} messages from DLQ",
    )
