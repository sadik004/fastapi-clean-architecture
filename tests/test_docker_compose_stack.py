from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]

REPO_ROOT = Path(__file__).parent.parent
COMPOSE_FILE = REPO_ROOT / "docker-compose.yml"
PROMETHEUS_CONFIG_FILE = REPO_ROOT / "deployments" / "docker" / "prometheus.yml"


def _load_compose() -> dict[str, Any]:
    assert COMPOSE_FILE.is_file(), f"docker-compose.yml not found at {COMPOSE_FILE}"
    content = COMPOSE_FILE.read_text(encoding="utf-8")
    data = yaml.safe_load(content)
    assert isinstance(data, dict), "docker-compose.yml must parse into a mapping"
    return data


def _load_prometheus_config() -> dict[str, Any]:
    assert PROMETHEUS_CONFIG_FILE.is_file(), f"prometheus.yml not found at {PROMETHEUS_CONFIG_FILE}"
    content = PROMETHEUS_CONFIG_FILE.read_text(encoding="utf-8")
    data = yaml.safe_load(content)
    assert isinstance(data, dict), "prometheus.yml must parse into a mapping"
    return data


def test_compose_file_valid_yaml() -> None:
    """Verify docker-compose.yml is valid YAML and defines version and core sections."""
    compose = _load_compose()
    assert "services" in compose, "docker-compose.yml must declare a 'services' block"
    assert "volumes" in compose, "docker-compose.yml must declare a 'volumes' block"
    assert "networks" in compose, "docker-compose.yml must declare a 'networks' block"


def test_required_services_topology() -> None:
    """Invariant: All 4 ecosystem services (app, postgres, redis, prometheus) must be declared."""
    compose = _load_compose()
    services = compose.get("services", {})
    expected_services = {"app", "postgres", "redis", "prometheus"}
    assert expected_services.issubset(set(services.keys())), (
        f"Missing required services. Expected {expected_services}, found {set(services.keys())}"
    )


def test_service_healthy_dependency_sequencing() -> None:
    """Startup Race Condition Prevention:

    FastAPI 'app' service must strictly wait until postgres and redis are healthy (condition: service_healthy).
    Bare depends_on without healthcheck conditions causes catastrophic ConnectionRefusedError on startup.
    """
    compose = _load_compose()
    app = compose["services"]["app"]
    depends_on = app.get("depends_on", {})
    assert isinstance(depends_on, dict), "app.depends_on must be an object specifying conditions"

    assert "postgres" in depends_on, "app must depend on postgres"
    assert depends_on["postgres"].get("condition") == "service_healthy", (
        "app.depends_on.postgres must have condition: service_healthy"
    )

    assert "redis" in depends_on, "app must depend on redis"
    assert depends_on["redis"].get("condition") == "service_healthy", (
        "app.depends_on.redis must have condition: service_healthy"
    )


def test_stateful_services_volume_persistence() -> None:
    """Data Durability Invariant: Stateful containers must mount named persistent volumes."""
    compose = _load_compose()
    top_volumes = compose.get("volumes", {})
    assert "postgres_data" in top_volumes, "Named volume 'postgres_data' missing from top-level volumes"
    assert "redis_data" in top_volumes, "Named volume 'redis_data' missing from top-level volumes"
    assert "prometheus_data" in top_volumes, "Named volume 'prometheus_data' missing from top-level volumes"

    # Verify postgres mounts postgres_data
    postgres_volumes = compose["services"]["postgres"].get("volumes", [])
    assert any("postgres_data:/var/lib/postgresql/data" in str(v) for v in postgres_volumes), (
        "postgres must mount postgres_data to /var/lib/postgresql/data"
    )

    # Verify redis mounts redis_data
    redis_volumes = compose["services"]["redis"].get("volumes", [])
    assert any("redis_data:/data" in str(v) for v in redis_volumes), (
        "redis must mount redis_data to /data"
    )

    # Verify prometheus mounts prometheus_data
    prom_volumes = compose["services"]["prometheus"].get("volumes", [])
    assert any("prometheus_data:/prometheus" in str(v) for v in prom_volumes), (
        "prometheus must mount prometheus_data to /prometheus"
    )


def test_stateful_services_healthchecks_defined() -> None:
    """Healthcheck Invariant: Postgres and Redis must configure healthcheck probes."""
    compose = _load_compose()

    # Postgres pg_isready healthcheck
    pg_health = compose["services"]["postgres"].get("healthcheck", {})
    assert pg_health, "postgres service must define healthcheck"
    assert "pg_isready" in str(pg_health.get("test", "")), "postgres healthcheck must invoke pg_isready"

    # Redis ping healthcheck
    redis_health = compose["services"]["redis"].get("healthcheck", {})
    assert redis_health, "redis service must define healthcheck"
    assert "redis-cli" in str(redis_health.get("test", "")) and "ping" in str(redis_health.get("test", "")), (
        "redis healthcheck must invoke redis-cli ping"
    )


def test_network_isolation_and_port_exposure() -> None:
    """Network Security Invariant:

    Databases (Postgres, Redis) must not expose raw ports to the host in production.
    Only the edge services (app on 8000, prometheus on 9090) should publish host ports.
    """
    compose = _load_compose()
    services = compose["services"]

    # Postgres and Redis must have no published host ports
    assert "ports" not in services["postgres"], (
        "Security violation: postgres port must not be exposed to host in production"
    )
    assert "ports" not in services["redis"], (
        "Security violation: redis port must not be exposed to host in production"
    )

    # App exposes 8000 and Prometheus exposes 9090
    assert any("8000:8000" in str(p) for p in services["app"].get("ports", [])), (
        "app must expose port 8000:8000"
    )
    assert any("9090:9090" in str(p) for p in services["prometheus"].get("ports", [])), (
        "prometheus must expose port 9090:9090"
    )

    # All services must be bound to internal_network
    for svc_name in ["app", "postgres", "redis", "prometheus"]:
        svc_networks = services[svc_name].get("networks", [])
        assert "internal_network" in svc_networks, f"Service {svc_name} must join internal_network"


def test_prometheus_scrape_configuration() -> None:
    """Telemetry Invariant: Prometheus must scrape FastAPI /metrics on port 8000."""
    prom_cfg = _load_prometheus_config()
    scrape_configs = prom_cfg.get("scrape_configs", [])
    assert len(scrape_configs) >= 1, "Prometheus config must define at least one scrape job"

    fastapi_job = next((j for j in scrape_configs if j.get("job_name") == "fastapi"), None)
    assert fastapi_job is not None, "Missing 'fastapi' scrape job in prometheus.yml"
    assert fastapi_job.get("metrics_path") == "/metrics", "fastapi scrape job must query /metrics"

    static_configs = fastapi_job.get("static_configs", [])
    assert len(static_configs) >= 1, "fastapi job must define static_configs"
    targets = static_configs[0].get("targets", [])
    assert any("app:8000" in str(t) for t in targets), "Prometheus target must be 'app:8000'"
