"""Test Suite for Structured JSON Logging, Correlation ID Propagation, and PII Redaction."""

from __future__ import annotations

import asyncio
import io
import json
import logging
import uuid
from collections.abc import Generator
from typing import Any

import pytest
import structlog
from fastapi.testclient import TestClient

from app.core.context import get_correlation_id, reset_correlation_id, set_correlation_id
from app.core.logging import (
    REDACTED_STR,
    get_logger,
    is_sensitive_key,
    redact_sensitive_data_processor,
    setup_logging,
)
from app.main import app


@pytest.fixture(autouse=True)
def configure_test_logging() -> Generator[tuple[io.StringIO, logging.Handler]]:
    """Configure structlog in production JSON mode and capture log stream in memory."""
    buffer = io.StringIO()
    setup_logging(environment="production", force_json=True)

    root_logger = logging.getLogger()
    old_handlers = list(root_logger.handlers)
    old_level = root_logger.level

    handler = logging.StreamHandler(buffer)
    if root_logger.handlers:
        handler.setFormatter(root_logger.handlers[0].formatter)
    root_logger.handlers = [handler]
    root_logger.setLevel(logging.DEBUG)

    yield buffer, handler

    root_logger.handlers = old_handlers
    root_logger.setLevel(old_level)


def _get_log_records(buffer: io.StringIO) -> list[dict[str, Any]]:
    """Parse newline-delimited JSON records from the log buffer."""
    records: list[dict[str, Any]] = []
    for line in buffer.getvalue().strip().split("\n"):
        line = line.strip()
        if line:
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return records


def test_json_output_formatting(configure_test_logging: tuple[io.StringIO, logging.Handler]) -> None:
    """Assert emitted logs format as valid JSON strings with required fields."""
    buffer, _ = configure_test_logging
    logger = get_logger("test.formatter")

    logger.info("payment_processed", transaction_id="tx_12345", amount=150.75)

    records = _get_log_records(buffer)
    matching = [r for r in records if r.get("event") == "payment_processed"]
    assert len(matching) == 1
    log_entry = matching[0]

    assert log_entry["event"] == "payment_processed"
    assert log_entry["level"] == "info"
    assert "timestamp" in log_entry
    assert log_entry["transaction_id"] == "tx_12345"
    assert log_entry["amount"] == 150.75


def test_correlation_id_propagation_via_middleware(
    configure_test_logging: tuple[io.StringIO, logging.Handler],
) -> None:
    """Dispatch HTTP request; assert all logs emitted during execution contain matching correlation_id."""
    buffer, _ = configure_test_logging
    client = TestClient(app)
    custom_cid = f"test-cid-{uuid.uuid4()}"

    response = client.get("/health", headers={"X-Correlation-ID": custom_cid})

    assert response.status_code == 200
    assert response.headers.get("X-Correlation-ID") == custom_cid
    assert response.headers.get("X-Request-ID") == custom_cid

    records = _get_log_records(buffer)
    app_records = [r for r in records if r.get("event") in ("http_request_started", "http_request_completed")]

    assert len(app_records) >= 2
    for record in app_records:
        assert record.get("correlation_id") == custom_cid
        assert record.get("path") == "/health"
        assert record.get("method") == "GET"


def test_correlation_id_auto_generation_uuidv7(
    configure_test_logging: tuple[io.StringIO, logging.Handler],
) -> None:
    """Verify that omitting correlation ID headers generates a valid RFC 9562 UUIDv7."""
    buffer, _ = configure_test_logging
    client = TestClient(app)

    response = client.get("/health")
    assert response.status_code == 200

    generated_cid = response.headers.get("X-Correlation-ID")
    assert generated_cid is not None
    # Validate UUID structure
    parsed_uuid = uuid.UUID(generated_cid)
    # UUIDv7 has version 7
    assert parsed_uuid.version == 7

    records = _get_log_records(buffer)
    app_records = [r for r in records if r.get("correlation_id") == generated_cid]
    assert len(app_records) >= 2


def test_pii_sensitive_data_redaction(
    configure_test_logging: tuple[io.StringIO, logging.Handler],
) -> None:
    """Log an event with sensitive keys; assert emitted JSON replaces values with [REDACTED]."""
    buffer, _ = configure_test_logging
    logger = get_logger("test.security")

    logger.info(
        "auth_attempt",
        username="john_doe",
        password="super_secret_password_123",
        token="eyJh...jwt_token",
        secret="vault_secret_999",
        authorization="Bearer access_token_xyz",
        credit_card="4111-2222-3333-4444",
        nested_meta={"user_password": "nested_secret", "safe_note": "account created"},
    )

    records = _get_log_records(buffer)
    matching = [r for r in records if r.get("event") == "auth_attempt"]
    assert len(matching) == 1
    log_entry = matching[0]

    assert log_entry["username"] == "john_doe"
    assert log_entry["password"] == REDACTED_STR
    assert log_entry["token"] == REDACTED_STR
    assert log_entry["secret"] == REDACTED_STR
    assert log_entry["authorization"] == REDACTED_STR
    assert log_entry["credit_card"] == REDACTED_STR
    assert log_entry["nested_meta"]["user_password"] == REDACTED_STR
    assert log_entry["nested_meta"]["safe_note"] == "account created"


def test_sensitive_key_detection_helper() -> None:
    """Verify O(1) sensitive key matcher covers canonical keys and suffixes."""
    assert is_sensitive_key("password")
    assert is_sensitive_key("token")
    assert is_sensitive_key("secret")
    assert is_sensitive_key("authorization")
    assert is_sensitive_key("credit_card")
    assert is_sensitive_key("user_password")
    assert is_sensitive_key("api_key")
    assert is_sensitive_key("client_secret")
    assert not is_sensitive_key("username")
    assert not is_sensitive_key("status")
    assert not is_sensitive_key("created_at")


def test_redact_processor_direct() -> None:
    """Direct verification of redact_sensitive_data_processor with event_dict."""
    event_dict: dict[str, Any] = {
        "event": "user_login",
        "password": "plain_password",
        "token": "secret_token",
        "details": [{"client_secret": "raw_secret"}, {"safe_id": 100}],
    }
    processed = redact_sensitive_data_processor(None, "info", event_dict)
    assert processed["password"] == REDACTED_STR
    assert processed["token"] == REDACTED_STR
    assert processed["details"][0]["client_secret"] == REDACTED_STR
    assert processed["details"][1]["safe_id"] == 100


@pytest.mark.asyncio
async def test_asyncio_task_context_isolation() -> None:
    """Verify that concurrent asyncio tasks maintain strict correlation ID isolation."""
    results: list[tuple[str, str]] = []

    async def worker(task_name: str, cid: str) -> None:
        token = set_correlation_id(cid)
        structlog.contextvars.bind_contextvars(correlation_id=cid)
        await asyncio.sleep(0.01)
        active_cid = get_correlation_id()
        results.append((task_name, active_cid))
        structlog.contextvars.clear_contextvars()
        reset_correlation_id(token)

    tasks = [
        worker("task_1", "cid-task-111"),
        worker("task_2", "cid-task-222"),
        worker("task_3", "cid-task-333"),
    ]
    await asyncio.gather(*tasks)

    results_dict = dict(results)
    assert results_dict["task_1"] == "cid-task-111"
    assert results_dict["task_2"] == "cid-task-222"
    assert results_dict["task_3"] == "cid-task-333"


def test_contextvars_cleanup_after_request(
    configure_test_logging: tuple[io.StringIO, logging.Handler],
) -> None:
    """Verify contextvars are strictly cleaned up after HTTP request completes."""
    client = TestClient(app)
    response = client.get("/health", headers={"X-Correlation-ID": "test-clean-cid"})
    assert response.status_code == 200

    # Outside the request, correlation ID must be empty and not leaked
    assert get_correlation_id() == ""


def test_observability_probe_endpoint(
    configure_test_logging: tuple[io.StringIO, logging.Handler],
) -> None:
    """Verify GET /observability/logging/probe emits logs across levels."""
    buffer, _ = configure_test_logging
    client = TestClient(app)
    cid = f"probe-cid-{uuid.uuid4()}"

    response = client.get("/observability/logging/probe", headers={"X-Correlation-ID": cid})
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    assert data["correlation_id"] == cid

    records = _get_log_records(buffer)
    probe_events = {r.get("event") for r in records if r.get("correlation_id") == cid}
    assert "probe_info_event" in probe_events
    assert "probe_warning_event" in probe_events
    assert "probe_error_event" in probe_events


def test_observability_simulate_error_endpoint(
    configure_test_logging: tuple[io.StringIO, logging.Handler],
) -> None:
    """Verify POST /observability/logging/simulate-error emits structured error with PII redaction."""
    buffer, _ = configure_test_logging
    client = TestClient(app)
    cid = f"error-cid-{uuid.uuid4()}"

    response = client.post("/observability/logging/simulate-error", headers={"X-Correlation-ID": cid})
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "simulated_error_emitted"
    assert data["correlation_id"] == cid

    records = _get_log_records(buffer)
    error_records = [r for r in records if r.get("event") == "simulated_error_event" and r.get("correlation_id") == cid]
    assert len(error_records) == 1
    err_entry = error_records[0]

    assert err_entry["level"] == "error"
    assert err_entry["password"] == REDACTED_STR
    assert err_entry["token"] == REDACTED_STR
    assert err_entry["authorization"] == REDACTED_STR
    assert err_entry["secret"] == REDACTED_STR
    assert err_entry["credit_card"] == REDACTED_STR
    assert err_entry["user_metadata"]["user_password"] == REDACTED_STR
    assert err_entry["user_metadata"]["username"] == "alice"
    assert "exception" in err_entry or "exc_info" in err_entry or "stack" in str(err_entry)


def test_stdlib_logging_interceptor(
    configure_test_logging: tuple[io.StringIO, logging.Handler],
) -> None:
    """Verify standard library logging is intercepted into structlog JSON format."""
    buffer, _ = configure_test_logging
    std_logger = logging.getLogger("legacy.stdlib.module")

    std_logger.info("Legacy standard library log event")

    records = _get_log_records(buffer)
    matching = [r for r in records if "Legacy standard library log event" in str(r.get("event", ""))]
    assert len(matching) == 1
    record = matching[0]
    assert record["level"] == "info"
    assert "timestamp" in record
