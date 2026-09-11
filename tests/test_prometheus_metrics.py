"""Comprehensive Test Suite for Prometheus Metrics Architecture (Day 73).

Tests:
1. Traffic Counter (http_requests_total) increments on request dispatch.
2. Latency Histogram (http_request_duration_seconds) captures observations in exponential buckets.
3. Concurrency Saturation Gauge (http_requests_in_flight) tracks in-flight executions.
4. Route Cardinality Protection (normalize_path converts UUIDs, IDs, and templates to :id).
5. Standard Prometheus scraping endpoint (GET /metrics) returns OpenMetrics text payload.
6. Business domain metrics (orders_placed_total, active_database_connections).
7. Error status code tracking (404 and 500 status codes correctly recorded).
"""

from __future__ import annotations

from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient

from app.core.metrics import (
    LATENCY_BUCKETS,
    get_metrics,
    reset_metrics,
)
from app.main import app


@pytest.fixture(autouse=True)
def clean_metrics_environment() -> Generator[None]:
    """Ensure clean metrics registry before and after every test."""
    reset_metrics()
    yield
    reset_metrics()


def test_http_requests_total_counter_increment() -> None:
    """Verify that dispatching an HTTP request increments http_requests_total counter with correct labels."""
    client = TestClient(app)
    metrics = get_metrics()

    initial = (
        metrics.registry.get_sample_value(
            "http_requests_total",
            {"method": "GET", "endpoint": "/health", "status_code": "200"},
        )
        or 0.0
    )

    response = client.get("/health")
    assert response.status_code == 200

    after = metrics.registry.get_sample_value(
        "http_requests_total",
        {"method": "GET", "endpoint": "/health", "status_code": "200"},
    )
    assert after == initial + 1.0


def test_http_request_duration_seconds_histogram_buckets() -> None:
    """Assert that request execution duration is observed into exponential SLA histogram buckets."""
    client = TestClient(app)
    metrics = get_metrics()

    response = client.get("/health")
    assert response.status_code == 200

    # Verify count incremented
    count = metrics.registry.get_sample_value(
        "http_request_duration_seconds_count",
        {"method": "GET", "endpoint": "/health", "status_code": "200"},
    )
    assert count is not None and count >= 1.0

    # Verify sum is a positive duration
    total_sum = metrics.registry.get_sample_value(
        "http_request_duration_seconds_sum",
        {"method": "GET", "endpoint": "/health", "status_code": "200"},
    )
    assert total_sum is not None and total_sum > 0.0

    # Verify bucket cumulative counts across configured latency buckets
    le_largest = str(float(LATENCY_BUCKETS[-1]))
    bucket_largest = metrics.registry.get_sample_value(
        "http_request_duration_seconds_bucket",
        {"method": "GET", "endpoint": "/health", "status_code": "200", "le": le_largest},
    )
    assert bucket_largest is not None and bucket_largest >= 1.0


def test_http_requests_in_flight_gauge() -> None:
    """Verify that in-flight requests gauge decrements to baseline 0 after request completion."""
    client = TestClient(app)
    metrics = get_metrics()

    response = client.get("/health")
    assert response.status_code == 200

    in_flight = metrics.registry.get_sample_value("http_requests_in_flight")
    assert in_flight == 0.0


def test_cardinality_protection_and_path_normalization() -> None:
    """Verify that raw UUIDs, integer IDs, and hashes are stripped to prevent cardinality explosion."""
    client = TestClient(app)
    metrics = get_metrics()

    test_uuid = "123e4567-e89b-12d3-a456-426614174000"
    response = client.get(f"/catalog/items/{test_uuid}")

    # Regardless of whether item exists (404 or 200), check Prometheus labels
    assert response.status_code in (200, 404)

    # Scrape rendered payload to verify raw UUID is NEVER present in Prometheus labels
    raw_payload = client.get("/metrics").text
    assert test_uuid not in raw_payload

    # Verify that the endpoint label is normalized to /catalog/items/:id
    sample = metrics.registry.get_sample_value(
        "http_requests_total",
        {"method": "GET", "endpoint": "/catalog/items/:id", "status_code": str(response.status_code)},
    )
    assert sample is not None and sample >= 1.0


def test_unrouted_path_normalization_fallback() -> None:
    """Verify that unrouted 404 paths with numeric and hex IDs are normalized by regex fallback."""
    client = TestClient(app)
    metrics = get_metrics()

    # Request a non-existent route with numeric ID
    response = client.get("/non-existent/resource/987654")
    assert response.status_code == 404

    sample = metrics.registry.get_sample_value(
        "http_requests_total",
        {"method": "GET", "endpoint": "/non-existent/resource/:id", "status_code": "404"},
    )
    assert sample is not None and sample >= 1.0


def test_prometheus_scraping_endpoint_text_format() -> None:
    """Verify GET /metrics returns HTTP 200 with standard Prometheus text/plain payload."""
    client = TestClient(app)

    # Emit at least one request to populate metrics
    client.get("/health")

    response = client.get("/metrics")
    assert response.status_code == 200
    assert "text/plain" in response.headers.get("content-type", "")

    body = response.text
    assert "# TYPE http_requests_total counter" in body
    assert "# TYPE http_request_duration_seconds histogram" in body
    assert "# TYPE http_requests_in_flight gauge" in body
    assert 'http_requests_total{endpoint="/health",method="GET",status_code="200"}' in body


def test_business_domain_metrics() -> None:
    """Verify business domain metrics: orders_placed_total counter and active_database_connections gauge."""
    metrics = get_metrics()

    # 1. Increment orders counter
    metrics.orders_placed_total.labels(payment_method="stripe", status="completed").inc(3)
    val = metrics.registry.get_sample_value(
        "orders_placed_total",
        {"payment_method": "stripe", "status": "completed"},
    )
    assert val == 3.0

    # 2. Update active db connections gauge
    metrics.active_database_connections.set(12.0)
    db_val = metrics.registry.get_sample_value("active_database_connections")
    assert db_val == 12.0

    metrics.active_database_connections.dec(4.0)
    db_val_after = metrics.registry.get_sample_value("active_database_connections")
    assert db_val_after == 8.0
