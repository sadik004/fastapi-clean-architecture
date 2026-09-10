"""Enterprise test suite for Grafana Dashboards as Code & PromQL Time-Series Metrics.

Verifies:
1. Dashboard JSON schema compliance (Grafana 10/11, schemaVersion >= 36).
2. Panel PromQL queries with proper lookback ranges and the 4 Golden Signals.
3. DashboardService validation and tamper detection.
4. HTTP endpoints /observability/dashboards/golden-signals and /validate.
5. Sub-5ms O(N_panels) validation performance.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services.dashboard_service import DashboardService, DashboardValidationError

_DASHBOARD_FILE: Path = (
    Path(__file__).resolve().parent.parent
    / "app"
    / "core"
    / "observability"
    / "dashboards"
    / "fastapi_golden_signals.json"
)


@pytest.fixture
def client() -> TestClient:
    """Synchronous test client for observability dashboard routes."""
    return TestClient(app)


@pytest.fixture
def dashboard_service() -> DashboardService:
    """DashboardService instance for testing."""
    return DashboardService()


def test_dashboard_json_schema_validity() -> None:
    """Assert that fastapi_golden_signals.json exists and conforms to Grafana 10/11 schema."""
    assert _DASHBOARD_FILE.exists(), f"Dashboard JSON missing at {_DASHBOARD_FILE}"

    with open(_DASHBOARD_FILE, encoding="utf-8") as f:
        data: Any = json.load(f)

    assert isinstance(data, dict)
    assert data.get("schemaVersion", 0) >= 36
    assert data.get("title") == "FastAPI Production Architecture - Golden Signals & SLA"
    assert data.get("uid") == "fastapi-golden-signals"
    assert data.get("editable") is True
    assert "fastapi" in data.get("tags", [])
    assert "golden-signals" in data.get("tags", [])

    panels: list[dict[str, Any]] = data.get("panels", [])
    assert len(panels) >= 6

    # Verify unique panel IDs
    panel_ids = [p["id"] for p in panels]
    assert len(panel_ids) == len(set(panel_ids)), "Panel IDs must be strictly unique"


def test_panel_promql_golden_signals_and_sla() -> None:
    """Verify all 4 Golden Signals and SLA metrics exist with valid PromQL expressions."""
    with open(_DASHBOARD_FILE, encoding="utf-8") as f:
        data: dict[str, Any] = json.load(f)

    panels: list[dict[str, Any]] = data["panels"]
    all_exprs: list[str] = []
    for panel in panels:
        for target in panel.get("targets", []):
            all_exprs.append(target.get("expr", ""))

    expr_text = " ".join(all_exprs)

    # 1. Traffic (sum(rate(http_requests_total[1m])))
    assert "rate(http_requests_total[1m])" in expr_text
    # 2. Error Rate & Availability SLA (5xx rate over total rate)
    assert "5.." in expr_text and "http_requests_total" in expr_text
    # 3. Latency Percentiles (P50, P95, P99)
    assert "histogram_quantile(0.5" in expr_text
    assert "histogram_quantile(0.95" in expr_text
    assert "histogram_quantile(0.99" in expr_text
    assert "http_request_duration_seconds_bucket" in expr_text
    # 4. Saturation (http_requests_in_flight & DB connections)
    assert "http_requests_in_flight" in expr_text
    assert "active_database_connections" in expr_text
    # 5. Top Endpoints
    assert "topk(5" in expr_text


def test_dashboard_service_validation_success(dashboard_service: DashboardService) -> None:
    """Assert DashboardService successfully validates the production dashboard model."""
    is_valid = dashboard_service.validate_dashboard_schema()
    assert is_valid is True

    summary = dashboard_service.get_validation_summary()
    assert summary["status"] == "valid"
    assert summary["schema_version"] >= 36
    assert summary["panel_count"] >= 6
    assert summary["golden_signals_verified"] is True
    assert summary["validation_time_ms"] < 5.0, "Validation must run in sub-5ms"


def test_dashboard_service_tamper_detection(dashboard_service: DashboardService) -> None:
    """Verify DashboardService catches corrupted or invalid schema mutations."""
    valid_data = dashboard_service.load_dashboard_json()

    # 1. Outdated schemaVersion
    bad_schema = dict(valid_data, schemaVersion=30)
    with pytest.raises(DashboardValidationError, match="Invalid schemaVersion"):
        dashboard_service.validate_dashboard_schema(bad_schema)

    # 2. Empty title
    bad_title = dict(valid_data, title="")
    with pytest.raises(DashboardValidationError, match="Dashboard title must be a non-empty string"):
        dashboard_service.validate_dashboard_schema(bad_title)

    # 3. Duplicate panel IDs
    bad_panels = [dict(p) for p in valid_data["panels"]]
    bad_panels[1]["id"] = bad_panels[0]["id"]
    bad_dup_id = dict(valid_data, panels=bad_panels)
    with pytest.raises(DashboardValidationError, match="Duplicate panel id"):
        dashboard_service.validate_dashboard_schema(bad_dup_id)

    # 4. Unbalanced PromQL parentheses
    bad_promql_panels = [dict(p) for p in valid_data["panels"]]
    bad_promql_panels[0]["targets"] = [{"expr": "sum(rate(http_requests_total[1m])", "refId": "A"}]
    bad_promql = dict(valid_data, panels=bad_promql_panels)
    with pytest.raises(DashboardValidationError, match="unbalanced parentheses"):
        dashboard_service.validate_dashboard_schema(bad_promql)

    # 5. Missing lookback window on rate query
    bad_rate_panels = [dict(p) for p in valid_data["panels"]]
    bad_rate_panels[0]["targets"] = [{"expr": "sum(rate(http_requests_total))", "refId": "A"}]
    bad_rate = dict(valid_data, panels=bad_rate_panels)
    with pytest.raises(DashboardValidationError, match="missing range window"):
        dashboard_service.validate_dashboard_schema(bad_rate)


def test_export_dashboard_json(dashboard_service: DashboardService) -> None:
    """Assert export_dashboard_json returns deep copy without modifying internal state."""
    exported = dashboard_service.export_dashboard_json()
    assert exported["uid"] == "fastapi-golden-signals"
    assert len(exported["panels"]) >= 6

    # Mutate export and ensure cached copy in service remains pristine
    exported["title"] = "Mutated Title"
    clean_copy = dashboard_service.load_dashboard_json()
    assert clean_copy["title"] == "FastAPI Production Architecture - Golden Signals & SLA"


def test_http_api_golden_signals_endpoint(client: TestClient) -> None:
    """Test GET /observability/dashboards/golden-signals returns valid Grafana JSON."""
    response = client.get("/observability/dashboards/golden-signals")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/json"

    data = response.json()
    assert data["title"] == "FastAPI Production Architecture - Golden Signals & SLA"
    assert data["schemaVersion"] >= 36
    assert len(data["panels"]) >= 6


def test_http_api_validate_endpoint(client: TestClient) -> None:
    """Test GET /observability/dashboards/validate executes automated health check."""
    response = client.get("/observability/dashboards/validate")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "valid"
    assert data["golden_signals_verified"] is True
    assert data["schema_version"] >= 36
    assert data["panel_count"] >= 6
    assert data["validation_time_ms"] < 10.0


def test_validation_performance_o_n(dashboard_service: DashboardService) -> None:
    """Benchmark schema validation performance to guarantee O(N_panels) < 5ms."""
    # Warmup
    dashboard_service.validate_dashboard_schema()

    iterations = 20
    start = time.perf_counter()
    for _ in range(iterations):
        dashboard_service.validate_dashboard_schema()
    total_ms = (time.perf_counter() - start) * 1000
    avg_ms = total_ms / iterations

    assert avg_ms < 5.0, f"Average validation took {avg_ms:.3f}ms, expected < 5ms"
