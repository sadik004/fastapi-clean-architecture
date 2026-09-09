"""Pydantic schemas for Message Broker requests and responses."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class DirectPublishRequest(BaseModel):
    """Payload for publishing to a Direct exchange with exact routing key."""

    model_config = ConfigDict(frozen=True)

    routing_key: str = Field(
        ...,
        description="Exact routing key for direct exchange (e.g. 'order.created')",
        min_length=1,
        max_length=255,
        examples=["order.created"],
    )
    payload: dict[str, Any] = Field(
        ...,
        description="Arbitrary JSON-serializable message dictionary",
        examples=[{"order_id": "ord_12345", "amount": 250.0, "currency": "USD"}],
    )


class FanoutPublishRequest(BaseModel):
    """Payload for broadcasting an event to a Fanout exchange."""

    model_config = ConfigDict(frozen=True)

    payload: dict[str, Any] = Field(
        ...,
        description="Arbitrary JSON-serializable event dictionary to broadcast",
        examples=[{"event_id": "evt_987", "event_type": "user.registered", "user_id": "usr_99"}],
    )


class TopicPublishRequest(BaseModel):
    """Payload for publishing to a Topic exchange with hierarchical dot-separated key."""

    model_config = ConfigDict(frozen=True)

    routing_key: str = Field(
        ...,
        description="Dot-separated topic routing key (e.g. 'order.eu.critical' or 'europe.germany.info')",
        min_length=1,
        max_length=255,
        examples=["order.eu.critical"],
    )
    payload: dict[str, Any] = Field(
        ...,
        description="Arbitrary JSON-serializable log/event payload",
        examples=[{"log_level": "CRITICAL", "region": "EU", "message": "Database latency spike"}],
    )


class BrokerPublishResponse(BaseModel):
    """Unified HTTP 202 response for message publication."""

    model_config = ConfigDict(frozen=True)

    status: str = Field(default="PUBLISHED", description="Publication status")
    exchange: str = Field(..., description="Target exchange name")
    routing_key: str | None = Field(default=None, description="Routing key used, if any")
    delivery_mode: str = Field(default="PERSISTENT", description="AMQP message persistence guarantee")
    message: str = Field(..., description="Human-readable status confirmation")


class BrokerConsumeResponse(BaseModel):
    """Response returned when a consumer consumes and acknowledges a message."""

    model_config = ConfigDict(frozen=True)

    status: str = Field(default="CONSUMED", description="Consumption status")
    queue_name: str = Field(..., description="Source queue consumed from")
    routing_key: str = Field(..., description="Routing key embedded in message")
    exchange: str = Field(..., description="Exchange the message was published through")
    payload: dict[str, Any] = Field(..., description="Decoded message JSON body")
    acknowledged: bool = Field(default=True, description="Whether manual ACK was performed")
