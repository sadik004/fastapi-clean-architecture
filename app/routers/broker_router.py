"""FastAPI Router exposing AMQP 0-9-1 Message Broker endpoints."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from app.schemas.broker import (
    BrokerConsumeResponse,
    BrokerPublishResponse,
    DirectPublishRequest,
    FanoutPublishRequest,
    TopicPublishRequest,
)
from app.services.message_broker_service import MessageBrokerService

router = APIRouter(prefix="/broker", tags=["Message Broker (RabbitMQ)"])


@router.post(
    "/publish/direct",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=BrokerPublishResponse,
    summary="Publish message to Direct Exchange",
)
async def publish_direct(request: DirectPublishRequest) -> BrokerPublishResponse:
    """Publish a persistent message to the Direct exchange ('orders.direct') with exact routing key."""
    result = await MessageBrokerService.publish_direct_message(
        routing_key=request.routing_key,
        payload=request.payload,
    )
    return BrokerPublishResponse(**result)


@router.post(
    "/publish/fanout",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=BrokerPublishResponse,
    summary="Broadcast event across Fanout Exchange",
)
async def publish_fanout(request: FanoutPublishRequest) -> BrokerPublishResponse:
    """Broadcast event across all queues bound to the Fanout exchange ('events.fanout')."""
    result = await MessageBrokerService.publish_fanout_event(
        payload=request.payload,
    )
    return BrokerPublishResponse(**result)


@router.post(
    "/publish/topic",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=BrokerPublishResponse,
    summary="Publish message to Topic Exchange",
)
async def publish_topic(request: TopicPublishRequest) -> BrokerPublishResponse:
    """Publish hierarchical message to Topic exchange ('logs.topic') matching wildcard bindings."""
    result = await MessageBrokerService.publish_topic_message(
        routing_key=request.routing_key,
        payload=request.payload,
    )
    return BrokerPublishResponse(**result)


@router.post(
    "/consume/{queue_name}",
    status_code=status.HTTP_200_OK,
    response_model=BrokerConsumeResponse,
    summary="Fetch and acknowledge next message from queue",
)
async def consume_message(queue_name: str) -> BrokerConsumeResponse:
    """Fetch the next available message from the specified queue and perform manual acknowledgement."""
    msg = await MessageBrokerService.consume_next_message(queue_name=queue_name, auto_ack=False)
    if msg is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No messages available in queue '{queue_name}'.",
        )
    return BrokerConsumeResponse(**msg)
