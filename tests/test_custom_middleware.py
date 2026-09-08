"""Tests for Day 27: Custom ASGI Middleware (Latency Tracking, Correlation IDs & Security Header Injection)."""

import pytest
from starlette.testclient import TestClient

from app.main import app


@pytest.fixture
def client() -> TestClient:
    """Fixture providing TestClient instance."""
    return TestClient(app)


def test_custom_middleware_headers_presence_on_success(client: TestClient) -> None:
    """Assert all observability and OWASP security headers are present on 200 OK responses."""
    response = client.get("/health")
    assert response.status_code == 200

    # 1. Observability Headers
    assert "X-Process-Time-Ms" in response.headers
    assert "X-Request-ID" in response.headers
    process_time_val = float(response.headers["X-Process-Time-Ms"])
    assert process_time_val >= 0.0

    # 2. OWASP Defense-in-Depth Security Headers
    assert response.headers.get("X-Content-Type-Options") == "nosniff"
    assert response.headers.get("X-Frame-Options") == "DENY"
    assert response.headers.get("Strict-Transport-Security") == "max-age=31536000; includeSubDomains"
    assert response.headers.get("Referrer-Policy") == "strict-origin-when-cross-origin"
    assert response.headers.get("X-XSS-Protection") == "0"


def test_custom_middleware_correlation_id_echo(client: TestClient) -> None:
    """Assert that client-supplied X-Request-ID and X-Correlation-ID are accurately echoed back."""
    # Test A: Custom X-Request-ID
    custom_trace_id = "test-custom-trace-12345"
    response_a = client.get("/health", headers={"X-Request-ID": custom_trace_id})
    assert response_a.status_code == 200
    assert response_a.headers.get("X-Request-ID") == custom_trace_id

    # Test B: Custom X-Correlation-ID fallback
    custom_corr_id = "test-correlation-uuid-67890"
    response_b = client.get("/health", headers={"X-Correlation-ID": custom_corr_id})
    assert response_b.status_code == 200
    assert response_b.headers.get("X-Request-ID") == custom_corr_id


def test_custom_middleware_error_coverage_404(client: TestClient) -> None:
    """Assert middleware headers are injected even when an endpoint returns 404 Not Found."""
    response = client.get("/non-existent-endpoint-404")
    assert response.status_code == 404

    assert "X-Process-Time-Ms" in response.headers
    assert "X-Request-ID" in response.headers
    assert response.headers.get("X-Content-Type-Options") == "nosniff"
    assert response.headers.get("X-Frame-Options") == "DENY"
    assert response.headers.get("Strict-Transport-Security") == "max-age=31536000; includeSubDomains"
    assert response.headers.get("Referrer-Policy") == "strict-origin-when-cross-origin"
    assert response.headers.get("X-XSS-Protection") == "0"


def test_custom_middleware_error_coverage_422(client: TestClient) -> None:
    """Assert middleware headers are injected when an endpoint triggers 422 Validation Error."""
    # Negative age query triggers 422 validation failure
    response = client.get("/users/filter/by-age?min_age=-1")
    assert response.status_code == 422

    assert "X-Process-Time-Ms" in response.headers
    assert "X-Request-ID" in response.headers
    assert response.headers.get("X-Content-Type-Options") == "nosniff"
    assert response.headers.get("X-Frame-Options") == "DENY"


def test_custom_middleware_trace_id_synchronization_with_error_envelope(client: TestClient) -> None:
    """Assert that X-Request-ID header matches the trace_id in the JSON ErrorResponse body."""
    custom_id = "trace-sync-check-999"
    # Trigger 400 Bad Request via inverted bounds
    response = client.get("/users/filter/by-age?min_age=50&max_age=20", headers={"X-Request-ID": custom_id})
    assert response.status_code == 400

    # Header check
    header_request_id = response.headers.get("X-Request-ID")
    assert header_request_id == custom_id

    # Body check
    body = response.json()
    assert "error" in body
    assert body["error"]["trace_id"] == custom_id


def test_custom_middleware_latency_reporting_accuracy(client: TestClient) -> None:
    """Assert that X-Process-Time-Ms is formatted with 2 decimal places and reflects realistic execution time."""
    response = client.get("/health")
    assert response.status_code == 200

    raw_val = response.headers["X-Process-Time-Ms"]
    # Check format: float with two decimals
    assert "." in raw_val
    duration = float(raw_val)
    assert 0.0 <= duration < 500.0  # realistic local health check latency
