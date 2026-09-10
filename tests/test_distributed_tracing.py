"""Comprehensive Test Suite for OpenTelemetry Distributed Tracing Architecture (Day 72).

Tests:
1. Root & Child Span DAG Hierarchy and parent_span_id validation.
2. W3C traceparent header extraction, linking, and response header injection.
3. Trace-to-Log correlation (trace_id and span_id bound into structlog JSON records).
4. Automatic exception recording and StatusCode.ERROR marking.
5. @trace_span decorator execution across asynchronous coroutines and synchronous functions.
6. Tracing diagnostics API endpoints (/observability/tracing/order-flow and /spans/{trace_id}).
7. Concurrency isolation across asyncio tasks without span leakage.
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
from collections.abc import Generator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from opentelemetry.trace import StatusCode

from app.core.logging import setup_logging
from app.core.tracing import (
    clear_in_memory_spans,
    format_span_id,
    format_trace_id,
    get_in_memory_exporter,
    get_tracer,
    init_tracer,
    trace_span,
)
from app.main import app
from app.services.traced_order_service import TracedOrderService


@pytest.fixture(autouse=True)
def setup_tracing_test_environment() -> Generator[None]:
    """Ensure clean TracerProvider and empty in-memory span buffer for every test."""
    init_tracer(service_name="fastapi-test-suite", environment="test")
    clear_in_memory_spans()
    yield
    clear_in_memory_spans()


@pytest.fixture
def capture_logging_buffer() -> Generator[io.StringIO]:
    """Configure structlog in test mode and capture JSON records in memory."""
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

    yield buffer

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


@pytest.mark.asyncio
async def test_root_and_child_span_dag_hierarchy() -> None:
    """Verify that multi-step checkout workflow creates a valid parent-child Directed Acyclic Graph (DAG)."""
    service = TracedOrderService()
    result = await service.execute_checkout_flow(
        order_id="ord_test_dag_101",
        user_id="usr_alice_42",
        item_id="item_keyboard_rgb",
        quantity=2,
        amount=199.98,
    )

    assert result["status"] == "success"
    trace_id = result["trace_id"]
    parent_span_id = result["parent_span_id"]

    exporter = get_in_memory_exporter()
    spans = exporter.get_finished_spans()

    # Total spans: 1 parent (order.checkout) + 3 children (inventory.verify, payment.charge, kafka.dispatch)
    assert len(spans) == 4

    # All spans must share identical trace_id
    for s in spans:
        assert format_trace_id(s.context.trace_id) == trace_id

    # Find the parent span
    parent_spans = [s for s in spans if s.name == "order.checkout"]
    assert len(parent_spans) == 1
    parent = parent_spans[0]
    assert format_span_id(parent.context.span_id) == parent_span_id
    assert parent.parent is None or parent.parent.span_id is None or parent.parent.span_id == 0

    # Child spans must reference the parent's span_id
    child_names = {"inventory.verify", "payment.charge", "kafka.dispatch"}
    children = [s for s in spans if s.name in child_names]
    assert len(children) == 3

    for child in children:
        assert child.parent is not None
        assert format_span_id(child.parent.span_id) == parent_span_id


def test_w3c_traceparent_header_propagation_new_trace() -> None:
    """Verify that incoming requests without traceparent generate valid W3C header in response."""
    client = TestClient(app)
    response = client.get("/health")

    assert response.status_code == 200
    traceparent = response.headers.get("traceparent")
    assert traceparent is not None

    parts = traceparent.split("-")
    assert len(parts) == 4
    assert parts[0] == "00"  # Version
    assert len(parts[1]) == 32  # 16-byte trace-id in hex
    assert len(parts[2]) == 16  # 8-byte span-id in hex
    assert parts[3] == "01"  # Sampled flag


def test_w3c_traceparent_header_propagation_existing_trace() -> None:
    """Verify that incoming W3C traceparent header is correctly propagated and linked."""
    client = TestClient(app)
    client_trace_id = "4bf92f3577b34da6a3ce929d0e0e4736"
    client_span_id = "00f067aa0ba902b7"
    incoming_traceparent = f"00-{client_trace_id}-{client_span_id}-01"

    response = client.get("/health", headers={"traceparent": incoming_traceparent})

    assert response.status_code == 200
    res_traceparent = response.headers.get("traceparent")
    assert res_traceparent is not None

    parts = res_traceparent.split("-")
    assert parts[0] == "00"
    # The trace ID MUST match the client's distributed trace ID!
    assert parts[1] == client_trace_id
    # Server span ID must be a distinct valid 16-hex string
    assert len(parts[2]) == 16
    assert parts[2] != client_span_id


def test_trace_to_log_correlation(capture_logging_buffer: io.StringIO) -> None:
    """Assert that structured JSON logs emitted by middleware contain matching trace_id and span_id."""
    client = TestClient(app)
    client_trace_id = "a1b2c3d4e5f60718293a4b5c6d7e8f90"
    incoming_traceparent = f"00-{client_trace_id}-1122334455667788-01"

    response = client.get("/health", headers={"traceparent": incoming_traceparent})
    assert response.status_code == 200

    records = _get_log_records(capture_logging_buffer)
    middleware_records = [
        r for r in records if r.get("event") in ("http_request_started", "http_request_completed")
    ]

    assert len(middleware_records) >= 2
    for r in middleware_records:
        assert r.get("trace_id") == client_trace_id
        assert r.get("span_id") is not None
        assert len(str(r.get("span_id"))) == 16


@pytest.mark.asyncio
async def test_span_error_recording_and_status() -> None:
    """Verify that exceptions inside a span set StatusCode.ERROR and record exception details."""
    service = TracedOrderService()

    with pytest.raises(ValueError, match="Payment gateway declined"):
        await service.execute_checkout_flow(
            order_id="ord_fail_999",
            user_id="usr_bob_12",
            item_id="item_gpu",
            quantity=1,
            amount=799.00,
            fail_at_step="payment",
        )

    exporter = get_in_memory_exporter()
    spans = exporter.get_finished_spans()

    # Find the failed payment.charge span
    failed_payment_spans = [s for s in spans if s.name == "payment.charge"]
    assert len(failed_payment_spans) == 1
    payment_span = failed_payment_spans[0]

    assert payment_span.status.status_code == StatusCode.ERROR
    assert "insufficient funds" in str(payment_span.status.description)

    # Verify exception event is recorded on the span
    exception_events = [e for e in payment_span.events if e.name == "exception"]
    assert len(exception_events) >= 1
    assert "ValueError" in str(exception_events[0].attributes)


def test_trace_span_decorator_sync() -> None:
    """Verify @trace_span decorator on synchronous functions."""

    @trace_span("sync.calculation", attributes={"calc.type": "fibonacci", "calc.version": 1})
    def compute_sum(a: int, b: int) -> int:
        return a + b

    result = compute_sum(15, 25)
    assert result == 40

    exporter = get_in_memory_exporter()
    spans = [s for s in exporter.get_finished_spans() if s.name == "sync.calculation"]
    assert len(spans) == 1
    span = spans[0]
    assert span.status.status_code == StatusCode.OK or span.status.status_code == StatusCode.UNSET
    assert span.attributes is not None
    assert span.attributes.get("calc.type") == "fibonacci"
    assert span.attributes.get("calc.version") == 1


@pytest.mark.asyncio
async def test_trace_span_decorator_async() -> None:
    """Verify @trace_span decorator on asynchronous coroutines."""

    @trace_span("async.data_fetch", attributes={"io.source": "remote_api"})
    async def fetch_data() -> str:
        await asyncio.sleep(0.002)
        return "payload_data"

    result = await fetch_data()
    assert result == "payload_data"

    exporter = get_in_memory_exporter()
    spans = [s for s in exporter.get_finished_spans() if s.name == "async.data_fetch"]
    assert len(spans) == 1
    span = spans[0]
    assert span.attributes is not None
    assert span.attributes.get("io.source") == "remote_api"


def test_tracing_diagnostics_order_flow_endpoint() -> None:
    """Verify POST /observability/tracing/order-flow and GET /observability/tracing/spans/{trace_id}."""
    client = TestClient(app)

    # 1. Trigger order workflow
    response = client.post(
        "/observability/tracing/order-flow",
        json={
            "order_id": "ord_api_test_505",
            "user_id": "usr_charlie_77",
            "item_id": "prod_headset_01",
            "quantity": 1,
            "amount": 99.50,
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    trace_id = data["trace_id"]
    assert len(trace_id) == 32

    # 2. Retrieve recorded spans for that trace_id
    spans_response = client.get(f"/observability/tracing/spans/{trace_id}")
    assert spans_response.status_code == 200
    spans_data = spans_response.json()
    assert spans_data["trace_id"] == trace_id
    # Root server span + order.checkout + 3 children = 5 spans
    assert spans_data["total_spans"] >= 4

    names = {s["name"] for s in spans_data["spans"]}
    assert "order.checkout" in names
    assert "inventory.verify" in names
    assert "payment.charge" in names
    assert "kafka.dispatch" in names

    # 3. Purge spans
    purge_res = client.delete("/observability/tracing/spans")
    assert purge_res.status_code == 204

    # Confirm purge
    spans_after = client.get(f"/observability/tracing/spans/{trace_id}").json()
    assert spans_after["total_spans"] == 0


@pytest.mark.asyncio
async def test_asyncio_task_span_context_isolation() -> None:
    """Verify that concurrent asyncio tasks maintain completely isolated active span contexts."""
    tracer = get_tracer("test.concurrency")
    results: dict[str, str] = {}

    async def task_worker(task_id: str) -> None:
        with tracer.start_as_current_span(f"task.{task_id}") as span:
            ctx = span.get_span_context()
            tid = format_trace_id(ctx.trace_id)
            await asyncio.sleep(0.005)
            # Verify context didn't drift during await
            current_ctx = tracer.start_as_current_span(f"subtask.{task_id}")
            with current_ctx as subspan:
                sub_tid = format_trace_id(subspan.get_span_context().trace_id)
                assert sub_tid == tid
            results[task_id] = tid

    await asyncio.gather(
        task_worker("A"),
        task_worker("B"),
        task_worker("C"),
    )

    assert len(results) == 3
    # Each task must have a distinct, unique trace_id
    unique_traces = set(results.values())
    assert len(unique_traces) == 3
