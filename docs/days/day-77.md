# Day 77: Docker Compose Production Stack Orchestration (FastAPI + PostgreSQL + Redis + Prometheus)

## Overview
Engineered a production-grade multi-container stack orchestration using `docker-compose.yml` to spin up our entire backend microservice ecosystem with a single command:
1. **FastAPI Application**: Web API service with Day 76 Multi-Stage Dockerfile architecture.
2. **PostgreSQL 15 (Alpine)**: Primary transactional database with isolated internal bridge networking and named volume persistence.
3. **Redis 7 (Alpine)**: Distributed caching and token revocation state with Append-Only File (AOF) durability.
4. **Prometheus 2.45.0**: Time-series telemetry scraper pulling `/metrics` every 15 seconds.

---

## Architectural Highlights

### 1. Directed Acyclic Graph (DAG) Dependency Sequencing
- **Elimination of Startup Race Conditions**:
  Standard `depends_on: [postgres]` merely waits for the Postgres container process to be spawned, ignoring whether the database engine has finished initializing and listening on port 5432. This leads to `ConnectionRefusedError` and container crashes.
- **`condition: service_healthy` Solution**:
  ```yaml
  depends_on:
    postgres:
      condition: service_healthy
    redis:
      condition: service_healthy
  ```
  The Compose engine resolves the service dependency graph topologically and polls the stateful services' healthcheck probes:
  - Postgres: `pg_isready -U appuser -d appdb` (`interval: 5s`, `retries: 5`)
  - Redis: `redis-cli ping` (`interval: 5s`, `retries: 5`)
  FastAPI boots strictly after both backends return healthy status codes.

### 2. Network Isolation & Security Invariant
- Defined isolated user bridge network: `internal_network`.
- Database (`postgres`) and cache (`redis`) do **not** expose or publish ports to the host interface in production, completely eliminating unauthorized external scanning and brute-force intrusion.
- Only the public edge endpoints are bound to host ports:
  - FastAPI: `8000:8000`
  - Prometheus UI: `9090:9090`

### 3. Named Volume Data Durability
- Bound stateful storage to persistent named volumes:
  - `postgres_data:/var/lib/postgresql/data`
  - `redis_data:/data`
  - `prometheus_data:/prometheus`
- Containers can be torn down (`docker compose down`) and recreated (`docker compose up -d`) with zero data loss.

### 4. Prometheus Telemetry Scraping
- Authoritative scrape configuration in `deployments/docker/prometheus.yml`:
  ```yaml
  scrape_configs:
    - job_name: "fastapi"
      scrape_interval: 15s
      metrics_path: "/metrics"
      static_configs:
        - targets: ["app:8000"]
  ```
- Scrapes the 4 Golden Signals and Prometheus counters exposed by Day 73's `/metrics` endpoint.

---

## Test & Verification Results
- `tests/test_docker_compose_stack.py`:
  1. `test_compose_file_valid_yaml`: Validates YAML syntax and top-level schema.
  2. `test_required_services_topology`: Asserts existence of `app`, `postgres`, `redis`, `prometheus`.
  3. `test_service_healthy_dependency_sequencing`: Validates `condition: service_healthy`.
  4. `test_stateful_services_volume_persistence`: Validates named volumes.
  5. `test_stateful_services_healthchecks_defined`: Validates `pg_isready` and `redis-cli ping`.
  6. `test_network_isolation_and_port_exposure`: Asserts databases do not expose host ports.
  7. `test_prometheus_scrape_configuration`: Asserts Prometheus targets `app:8000` on `/metrics`.
- Full Phase 7 regression test suite (64 tests): **100% PASS** in 9.00s.
- `ruff check`: 0 errors.
- `mypy --strict`: 0 issues across 252 source files.
