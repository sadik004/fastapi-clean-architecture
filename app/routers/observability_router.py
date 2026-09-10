"""Observability Diagnostics Router for Structured Logging & Telemetry Probing."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter

from app.core.config import get_settings
from app.core.context import get_correlation_id
from app.core.logging import get_logger

router = APIRouter(prefix="/observability/logging", tags=["Observability"])
logger = get_logger("app.routers.observability")


@router.get(
    "/probe",
    summary="Probe structured logging across all log levels",
    response_model=dict[str, Any],
)
async def probe_logging() -> dict[str, Any]:
    """Emit diagnostic logs across DEBUG, INFO, WARNING, and ERROR levels.

    Demonstrates automated correlation ID propagation from contextvars.
    """
    cid = get_correlation_id()
    settings = get_settings()

    logger.debug("probe_debug_event", probe_type="diagnostic", detail="Structured debug trace")
    logger.info("probe_info_event", probe_type="diagnostic", status="operational")
    logger.warning("probe_warning_event", probe_type="diagnostic", threshold_exceeded=False)
    logger.error("probe_error_event", probe_type="diagnostic", sample_error_code="ERR_PROBE_TEST")

    return {
        "status": "success",
        "correlation_id": cid,
        "logging_mode": "structured_json" if settings.environment == "production" else "console",
        "timestamp": datetime.now(UTC).isoformat(),
        "message": "Emitted probe telemetry events across all log levels",
    }


@router.post(
    "/simulate-error",
    summary="Simulate error with structured stack trace and sensitive fields for redaction verification",
    response_model=dict[str, Any],
)
async def simulate_error() -> dict[str, Any]:
    """Emit an exception log with stack trace and sensitive fields to verify PII redaction."""
    cid = get_correlation_id()

    try:
        raise ValueError("Simulated production error for structured stack trace verification")
    except ValueError:
        logger.error(
            "simulated_error_event",
            password="super_secret_password_123",  # noqa: S106
            token="jwt_secret_token_abc456",  # noqa: S106
            authorization="Bearer sensitive_token",
            secret="top_secret_vault_key",  # noqa: S106
            credit_card="4111-2222-3333-4444",
            user_metadata={"username": "alice", "user_password": "nested_password_abc"},
            exc_info=True,
        )

    return {
        "status": "simulated_error_emitted",
        "correlation_id": cid,
        "redacted_fields": [
            "password",
            "token",
            "authorization",
            "secret",
            "credit_card",
            "user_password",
        ],
        "message": "Simulated error logged with stack trace and redacted sensitive fields",
    }
