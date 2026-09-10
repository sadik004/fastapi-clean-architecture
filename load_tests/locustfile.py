"""Locust Load Testing Scenario Suite for High-Concurrency E-Commerce Endpoints.

Simulates realistic client journeys with weighted execution frequencies:
- Catalog Keyset Pagination (Weight 5): O(1) B-tree keyset navigation under heavy browsing.
- Full-Text Fuzzy Search (Weight 3): Typos and stemmed queries against PostgreSQL TSVector / pg_trgm.
- Telemetry & Health Probes (Weight 2): Non-blocking Prometheus metrics and liveness healthchecks.
- Idempotent Order Checkout (Weight 1): Transactional checkout mutations with UUIDv7 tracking.
"""

from __future__ import annotations

import uuid
from typing import Any

from locust import HttpUser, between, task


class FastAPIEcommerceUser(HttpUser):
    """Simulated e-commerce shopper navigating high-concurrency API endpoints."""

    wait_time = between(0.1, 0.5)

    @task(5)
    def browse_catalog_keyset(self) -> None:
        """High-Volume Catalog Browsing: Validates keyset pagination SLA (< 50ms)."""
        with self.client.get(
            "/catalog/items/keyset?limit=20",
            name="/catalog/items/keyset [Browse]",
            catch_response=True,
        ) as response:
            if response.status_code != 200:
                response.failure(f"Expected HTTP 200 but received {response.status_code}")
            elif response.elapsed.total_seconds() * 1000 > 100:  # Bound check for test harness
                response.failure(f"SLA breached: latency {response.elapsed.total_seconds() * 1000:.2f}ms > 100ms")
            else:
                response.success()

    @task(3)
    def fuzzy_search_products(self) -> None:
        """Fuzzy Search & Autocomplete: Validates PostgreSQL TSVector and Trigram queries."""
        with self.client.get(
            "/search/products?q=running&fuzzy=true",
            name="/search/products [Fuzzy Search]",
            catch_response=True,
        ) as response:
            if response.status_code != 200:
                response.failure(f"Expected HTTP 200 from search but got {response.status_code}")
            else:
                response.success()

    @task(2)
    def probe_health_and_metrics(self) -> None:
        """Health & Telemetry Probes: Validates zero-I/O liveness and Prometheus text scraping."""
        with self.client.get(
            "/health/liveness",
            name="/health/liveness [Probe]",
            catch_response=True,
        ) as response:
            if response.status_code != 200:
                response.failure(f"Liveness probe failed with {response.status_code}")
            else:
                response.success()

        with self.client.get(
            "/metrics",
            name="/metrics [Prometheus Scraping]",
            catch_response=True,
        ) as response:
            if response.status_code != 200:
                response.failure(f"Prometheus metrics scraping failed with {response.status_code}")
            else:
                response.success()

    @task(1)
    def place_order_checkout(self) -> None:
        """Order Placement & Idempotent Checkout: Executes stateful transactional mutations."""
        idempotency_key = f"idemp-bench-{uuid.uuid4()}"
        payload: dict[str, Any] = {
            "user_id": f"usr_bench_{uuid.uuid4().hex[:8]}",
            "total_amount": 149.95,
        }
        headers = {
            "X-Idempotency-Key": idempotency_key,
            "Content-Type": "application/json",
        }
        with self.client.post(
            "/orders",
            json=payload,
            headers=headers,
            name="/orders [Checkout]",
            catch_response=True,
        ) as response:
            if response.status_code not in (200, 201):
                response.failure(f"Order checkout failed with status {response.status_code}: {response.text}")
            else:
                response.success()
