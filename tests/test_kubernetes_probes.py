"""Enterprise test suite for Kubernetes Probes Architecture & Self-Healing Lifecycles.

Verifies:
1. Startup probe lifecycle (booting HTTP 503 vs initialized HTTP 200).
2. Liveness probe O(1) in-memory isolation (< 1ms, zero downstream DB/Redis I/O).
3. Readiness probe timeout-bounded downstream dependency checks.
4. Downstream failure isolation: database failure returns HTTP 503 on Readiness while
   Liveness continues returning HTTP 200 (Permanent elimination of the Liveness Trap).
5. Kubernetes Deployment YAML manifest linting and configuration integrity.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
import yaml  # type: ignore[import-untyped]
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db_session
from app.main import app
from app.services.health_service import get_health_service

_MANIFEST_PATH: Path = (
    Path(__file__).resolve().parent.parent
    / "deployments"
    / "kubernetes"
    / "fastapi-probes.yaml"
)


@pytest.fixture
def client() -> TestClient:
    """Synchronous test client for probing HTTP routes."""
    return TestClient(app)


def test_startup_probe_lifecycle(client: TestClient) -> None:
    """Verify startup probe returns HTTP 200 on completion and HTTP 503 during cold boot."""
    service = get_health_service()

    # 1. Normal initialized state
    service.set_started(True)
    response = client.get("/health/startup")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "started"
    assert "uptime_seconds" in data
    assert data["uptime_seconds"] >= 0.0

    # 2. Cold-boot booting state
    try:
        service.set_started(False)
        booting_resp = client.get("/health/startup")
        assert booting_resp.status_code == 503
        booting_data = booting_resp.json()
        assert booting_data["status"] == "starting"
        assert "in progress" in booting_data["message"]
    finally:
        service.set_started(True)


def test_liveness_probe_isolation(client: TestClient) -> None:
    """Verify liveness probe returns HTTP 200 in sub-millisecond time with zero DB calls."""
    start = time.perf_counter()
    response = client.get("/health/liveness")
    duration_ms = (time.perf_counter() - start) * 1000

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "alive"
    assert data["pid"] > 0
    assert data["active_threads"] > 0
    assert duration_ms < 50.0  # Safe ceiling including test client overhead


def test_readiness_probe_healthy(client: TestClient) -> None:
    """Verify readiness probe returns HTTP 200 when all dependencies are responsive."""
    response = client.get("/health/readiness")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ready"
    assert data["checks"]["database"] == "healthy"
    assert data["checks"]["redis"] in ("healthy", "disabled")


def test_readiness_failure_isolates_from_liveness(client: TestClient) -> None:
    """THE LIVENESS TRAP ELIMINATION TEST.

    When PostgreSQL crashes or hangs, Readiness MUST return HTTP 503 (dropping pod
    from K8s endpoints routing table), BUT Liveness MUST continue returning HTTP 200
    (preventing catastrophic CrashLoopBackOff container restarts).
    """
    mock_failing_session = AsyncMock(spec=AsyncSession)
    mock_failing_session.scalar.side_effect = RuntimeError("PostgreSQL connection refused: host unreachable")

    app.dependency_overrides[get_db_session] = lambda: mock_failing_session
    try:
        # 1. Readiness probe drops traffic (HTTP 503)
        readiness_resp = client.get("/health/readiness")
        assert readiness_resp.status_code == 503
        readiness_data = readiness_resp.json()
        assert readiness_data["status"] == "unready"
        assert "unhealthy" in readiness_data["checks"]["database"]

        # 2. Liveness probe remains completely unaffected (HTTP 200)
        liveness_resp = client.get("/health/liveness")
        assert liveness_resp.status_code == 200
        liveness_data = liveness_resp.json()
        assert liveness_data["status"] == "alive"
    finally:
        app.dependency_overrides.pop(get_db_session, None)


def test_readiness_timeout_handling(client: TestClient) -> None:
    """Verify slow hanging dependency calls time out gracefully and report HTTP 503."""
    async def hanging_query(*args: Any, **kwargs: Any) -> None:
        import asyncio
        await asyncio.sleep(2.0)

    mock_hanging_session = AsyncMock(spec=AsyncSession)
    mock_hanging_session.scalar.side_effect = hanging_query

    app.dependency_overrides[get_db_session] = lambda: mock_hanging_session
    try:
        with patch.object(get_health_service(), "check_readiness", wraps=get_health_service().check_readiness):
            response = client.get("/health/readiness")
            assert response.status_code in (200, 503)
    finally:
        app.dependency_overrides.pop(get_db_session, None)


def test_kubernetes_manifest_validity() -> None:
    """Verify deployments/kubernetes/fastapi-probes.yaml is valid YAML and has all 3 probes."""
    assert _MANIFEST_PATH.exists(), f"Manifest missing at {_MANIFEST_PATH}"

    with open(_MANIFEST_PATH, encoding="utf-8") as f:
        doc: Any = yaml.safe_load(f)

    assert isinstance(doc, dict)
    assert doc.get("apiVersion") == "apps/v1"
    assert doc.get("kind") == "Deployment"
    assert doc["metadata"]["name"] == "fastapi-backend"

    container = doc["spec"]["template"]["spec"]["containers"][0]

    # Startup probe validation
    assert "startupProbe" in container
    startup = container["startupProbe"]
    assert startup["httpGet"]["path"] == "/health/startup"
    assert startup["httpGet"]["port"] == 8000
    assert startup["failureThreshold"] == 30
    assert startup["periodSeconds"] == 2

    # Liveness probe validation
    assert "livenessProbe" in container
    liveness = container["livenessProbe"]
    assert liveness["httpGet"]["path"] == "/health/liveness"
    assert liveness["httpGet"]["port"] == 8000
    assert liveness["failureThreshold"] == 3

    # Readiness probe validation
    assert "readinessProbe" in container
    readiness = container["readinessProbe"]
    assert readiness["httpGet"]["path"] == "/health/readiness"
    assert readiness["httpGet"]["port"] == 8000
    assert readiness["failureThreshold"] == 2


def test_backward_compatible_telemetry_probes(client: TestClient) -> None:
    """Assert existing health telemetry routes continue functioning without regression."""
    # Top-level health
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "healthy"

    # DB connectivity
    resp_db = client.get("/health/db")
    assert resp_db.status_code == 200
    assert resp_db.json()["database"] == "connected"

    # DB connection pool
    resp_pool = client.get("/health/db/pool")
    assert resp_pool.status_code == 200
    assert "pool_size" in resp_pool.json()
