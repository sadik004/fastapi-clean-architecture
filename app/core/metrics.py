"""Enterprise Prometheus Metrics Architecture.

Configures isolated Prometheus CollectorRegistry, low-cardinality HTTP traffic counters,
SLA latency histograms with exponential buckets, concurrency gauges, and business domain metrics.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)

if TYPE_CHECKING:
    from starlette.requests import Request

# Exponential & Standard SLA Latency Buckets (in seconds)
LATENCY_BUCKETS: tuple[float, ...] = (
    0.005,  # 5ms
    0.01,  # 10ms
    0.025,  # 25ms
    0.05,  # 50ms
    0.075,  # 75ms
    0.1,  # 100ms
    0.25,  # 250ms
    0.5,  # 500ms
    0.75,  # 750ms
    1.0,  # 1s
    2.5,  # 2.5s
    5.0,  # 5s
    7.5,  # 7.5s
    10.0,  # 10s
)

# Regular expressions for O(1) path normalization to defeat cardinality explosions
_UUID_REGEX = re.compile(r"/[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
_INTEGER_ID_REGEX = re.compile(r"/\d+")
_HEX_ID_REGEX = re.compile(r"/[0-9a-fA-F]{24,64}")
_ROUTE_PARAM_REGEX = re.compile(r"\{[a-zA-Z0-9_]+\}")


class PrometheusMetrics:
    """Encapsulates Prometheus collector registry and metric definitions."""

    def __init__(self, registry: CollectorRegistry | None = None) -> None:
        self.registry: CollectorRegistry = registry or CollectorRegistry(auto_describe=True)

        self.http_requests_total: Counter = Counter(
            "http_requests_total",
            "Total count of HTTP requests processed by the server",
            ["method", "endpoint", "status_code"],
            registry=self.registry,
        )

        self.http_request_duration_seconds: Histogram = Histogram(
            "http_request_duration_seconds",
            "HTTP request latency execution duration in seconds",
            ["method", "endpoint", "status_code"],
            buckets=LATENCY_BUCKETS,
            registry=self.registry,
        )

        self.http_requests_in_flight: Gauge = Gauge(
            "http_requests_in_flight",
            "Current number of concurrent HTTP requests being processed",
            registry=self.registry,
        )

        self.orders_placed_total: Counter = Counter(
            "orders_placed_total",
            "Total count of orders placed through the platform",
            ["payment_method", "status"],
            registry=self.registry,
        )

        self.active_database_connections: Gauge = Gauge(
            "active_database_connections",
            "Current active connections checked out from the database connection pool",
            registry=self.registry,
        )


# Global singleton instance
metrics = PrometheusMetrics()


def get_metrics() -> PrometheusMetrics:
    """Retrieve the global PrometheusMetrics instance."""
    return metrics


def reset_metrics() -> PrometheusMetrics:
    """Re-initialize the global metrics registry and collectors for test isolation."""
    global metrics
    metrics = PrometheusMetrics()
    return metrics


def normalize_path(request: Request) -> str:
    """Normalize request path to prevent Prometheus label high-cardinality explosions.

    Rules:
    1. If FastAPI matched a registered route with parameter templates (e.g. `/orders/{id}`),
       normalizes it to `/orders/:id`.
    2. Fallback for 404s or raw URLs strips UUIDs, integer IDs, and hashes to `:id`.
    """
    route = request.scope.get("route")
    if route is not None and hasattr(route, "path") and route.path:
        route_path: str = route.path
        # Normalize FastAPI `{item_id}` to `:id`
        return _ROUTE_PARAM_REGEX.sub(":id", route_path)

    # Fallback regex normalization on raw URL path
    raw_path = request.url.path
    normalized = _UUID_REGEX.sub("/:id", raw_path)
    normalized = _HEX_ID_REGEX.sub("/:id", normalized)
    normalized = _INTEGER_ID_REGEX.sub("/:id", normalized)
    return normalized


def generate_metrics_payload() -> tuple[bytes, str]:
    """Scrape and render all registered Prometheus metrics into text format.

    Returns:
        Tuple of (rendered_bytes, content_type_header).
    """
    return generate_latest(metrics.registry), CONTENT_TYPE_LATEST
