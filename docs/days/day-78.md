# Day 78: Graceful Shutdown & SIGTERM Handling Architecture (Zero-Dropped Requests & Connection Draining)

## Overview
Engineered an enterprise-grade 3-phase Graceful Shutdown and Connection Draining architecture to intercept POSIX OS signals (`SIGTERM`, `SIGINT`), eliminate in-flight request termination during rolling deployments, trip Kubernetes Readiness Probes, drain active TCP/HTTP connections cleanly, and flush external connection pools (PostgreSQL, Redis, Kafka, RabbitMQ) with zero dropped requests.

---

## Architectural Highlights

### 1. Three-Phase Graceful Shutdown Protocol
When Kubernetes or an orchestrator stops a container during a rolling update, it transmits `SIGTERM`. Without graceful draining, running HTTP requests are abruptly terminated with `HTTP 502 Bad Gateway` / `ECONNRESET`. Our architecture enforces a strict 3-phase sequence:

1. **Phase 1: Immediate Traffic Cutoff (Trip Readiness Probe)**
   - On intercepting `SIGTERM`/`SIGINT`, `ShutdownManager.initiate_shutdown()` flips `is_shutting_down = True`.
   - `/health/readiness` immediately returns `HTTP 503 Service Unavailable` with `status: "shutting_down"`.
   - Ingress controllers, Kubernetes Service Endpoints, and reverse proxies cease dispatching new incoming requests to this pod.

2. **Phase 2: In-Flight Connection Draining & Keep-Alive Disablement**
   - Active HTTP requests are tracked with thread-safe / asyncio-safe counters (`in_flight_requests`).
   - Requests arriving or processing during shutdown have `Connection: close` headers injected to tell HTTP/1.1 clients to gracefully close their TCP socket rather than reusing it via keep-alive.
   - The server enters `await shutdown_mgr.wait_for_drain(timeout=30.0)`: an asynchronous event-driven loop polling until `in_flight_requests == 0` or the safety timeout expires.

3. **Phase 3: State & Connection Pool Disposal**
   - Once all in-flight requests complete cleanly, the lifespan context proceeds to dispose external resources:
     - Flush event message queues (Kafka, RabbitMQ)
     - Close Redis cache connection pool (`await redis_client.close()`)
     - Dispose SQLAlchemy connection engines (`await engine.dispose()`)

---

## Core Components Implemented

### 1. `app/core/lifecycle.py`
- Implemented `ShutdownManager`:
  - `is_shutting_down: bool`: Atomic shutdown flag.
  - `in_flight_requests: int`: Real-time tracking of concurrent requests with bounded floor (`>= 0`).
  - `increment_in_flight()` & `decrement_in_flight()`: Clean request lifecycle hooks.
  - `initiate_shutdown()`: Flips state and logs structured shutdown notice.
  - `wait_for_drain(timeout=30.0)`: Event-driven asyncio draining loop.
  - `register_signal_handlers()`: Defensive, cross-platform signal registration (`signal.SIGINT`, `signal.SIGTERM`) with graceful fallbacks for Windows and worker threads.
  - `get_shutdown_manager()`: Singleton accessor.

### 2. `app/core/middleware.py`
- Enhanced global request middleware:
  - Calls `shutdown_mgr.increment_in_flight()` upon entering request dispatch.
  - Injects `Connection: close` into response headers whenever `is_shutting_down` is True.
  - Always calls `shutdown_mgr.decrement_in_flight()` inside a guaranteed `finally:` block.

### 3. `app/routers/health_router.py`
- Trip Readiness Probe: `/health/readiness` checks `shutdown_mgr.is_shutting_down` before checking backing services. If True, returns `HTTP 503 Service Unavailable`.
- Diagnostic Endpoint: Added `GET /health/shutdown-status` returning `is_shutting_down` status and current `in_flight_requests` count for monitoring and test verification.

### 4. `app/main.py`
- Wired `register_signal_handlers()` during application boot.
- Lifespan shutdown handler invokes `await shutdown_mgr.wait_for_drain(timeout=30.0)` prior to closing Redis and database pools.

---

## Verification & Testing
Created `tests/test_graceful_shutdown.py` covering:
1. `test_in_flight_counter_boundary_conditions`: Validates counter increment, decrement, and zero-floor boundary enforcement.
2. `test_readiness_probe_trips_to_503_during_shutdown`: Proves readiness probe immediately returns 503 upon shutdown initiation.
3. `test_connection_close_header_injected_during_shutdown`: Verifies `Connection: close` header injection on active responses.
4. `test_shutdown_status_diagnostic_endpoint`: Validates diagnostic endpoint reporting during live and draining states.
5. `test_wait_for_drain_immediate_when_zero`: Ensures zero latency when no in-flight requests remain.
6. `test_in_flight_draining_completion_zero_dropped_requests`: Simulates concurrent requests completing during shutdown with zero dropped requests.
7. `test_wait_for_drain_timeout_enforcement`: Asserts safety timeout ceiling to prevent hanging pods.
8. `test_cross_platform_signal_handler_registration`: Validates cross-platform OS signal handler registration without runtime crashes.

All 8 tests passed, and all 72 tests across Phase 7 passed with 0 failures.
