# Day 80: Load Testing & Performance Profiling Architecture (Locust / k6 Benchmarking, P95/P99 Latency & Bottleneck Profiling)

## Overview
Engineered an enterprise-grade automated Load Testing & Performance Profiling architecture using **Locust** and headless in-process benchmark harnesses to stress-test high-concurrency endpoints, calculate exact percentile latency distributions ($P50, P90, P95, P99$), and detect thread pool exhaustion and resource bottlenecks before production deployment.

---

## Architectural Principles & SLAs

1. **Quantile Over Average (P95/P99 Latency SLAs)**:
   - Average latency masks catastrophic tail-latency spikes in bimodal and skewed distributions.
   - Enforced SLA: $P95 \le 100\text{ms}$ under concurrent load with an error rate $< 0.1\%$.

2. **Little's Law Throughput Modeling ($L = \lambda W$)**:
   - In-flight concurrency ($L$) is mathematically bounded by the product of arrival throughput ($\lambda$, in req/s) and mean request duration ($W$, in seconds).
   - Prevents queue starvation and connection pool exhaustion by matching ASGI concurrency semaphores to worker pool capacity.

3. **Realistic User Journeys via Weighted Probabilistic Simulation**:
   - Rather than hammering a single endpoint in an artificial loop, user journeys simulate production client browsing patterns with weighted task distribution and stochastic think time (`between(0.1, 0.5)` seconds).

4. **Zero-Socket In-Process Profiling (`httpx.ASGITransport`)**:
   - Enables sub-millisecond, pure application performance benchmarking in headless CI/CD pipelines without incurring TCP loopback network overhead.

---

## Core Components Implemented

### 1. Locust Load Testing Scenario Suite (`load_tests/locustfile.py`)
- Defines `FastAPIEcommerceUser(HttpUser)` with 4 realistic client journeys (sum of weights = 11):
  - **Task 1: Keyset Pagination Browsing (Weight 5)**: `GET /catalog/items/keyset?limit=20` testing indexed cursor pagination.
  - **Task 2: Fuzzy Search (Weight 3)**: `GET /catalog/search/fuzzy?query=...` evaluating trigram indexing and CPU query latency.
  - **Task 3: Health & Prometheus Telemetry (Weight 2)**: `GET /health/liveness` and `GET /metrics` verifying zero-I/O overhead.
  - **Task 4: Order Placement & Checkout (Weight 1)**: `POST /orders/checkout` testing distributed locking and write transaction handling.

### 2. Headless Performance Profiler Service (`app/services/profiling_service.py`)
- `calculate_percentiles(latencies_ms)`: Calculates $P50, P90, P95, P99$, min, max, and mean latencies in $\mathcal{O}(N \log N)$ time using nearest-rank linear interpolation.
- `ProfilingService`:
  - `run_scenario()`: Executes concurrent request streams with `asyncio.Semaphore` bounding, precise timing via `time.perf_counter()`, and SLA pass/fail validation.
  - `run_full_profile_suite()`: Orchestrates multi-scenario suites across catalog, health, and metrics endpoints, tracking throughput (RPS) and recording latest telemetry reports.
- `get_profiling_service()`: Singleton dependency provider.

### 3. CLI Benchmark Runner Harness (`scripts/run_benchmarks.py`)
- Terminal utility for continuous integration pipelines that executes headless load suites against application endpoints and prints formatted ASCII performance tables with return code 0 on SLA pass and 1 on failure.

### 4. Telemetry & Profiling Router (`app/routers/profiling_router.py`)
- Mounted under prefix `/observability/benchmarks`:
  - `POST /observability/benchmarks/run-profile`: Triggers an on-demand profiling run with configurable concurrency and request volume.
  - `GET /observability/benchmarks/latest`: Exposes the most recent benchmark metrics report for monitoring and capacity dashboards.

### 5. Application Mounting (`app/main.py`)
- Mounted `profiling_router` under `/observability/benchmarks` with OpenAPI documentation tags.

---

## Verification & Automated Tests
Authored comprehensive test suite in `tests/test_performance_benchmarks.py`:
- `test_locustfile_task_weights_and_structure`: Validates AST structure and exact task weights (5:3:2:1) in `locustfile.py`.
- `test_locust_headless_execution_in_isolated_process`: Executes `locust --headless` in an isolated subprocess to verify runtime compatibility.
- `test_percentile_calculation_accuracy`: Mathematically validates quantile interpolation on synthetic latency sets.
- `test_concurrent_load_under_concurrency_scaling`: Simulates concurrent traffic against `/catalog/items/keyset` enforcing $P95$ SLA and 0% error rate.
- `test_throughput_threshold_in_memory`: Verifies high RPS throughput on zero-I/O in-memory endpoints.
- `test_benchmark_diagnostic_api_run_and_latest`: Asserts `POST /run-profile` and `GET /latest` API contracts and reporting metrics.
