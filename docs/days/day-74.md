# Day 74: Declarative Grafana Dashboards as Code & PromQL Time-Series Metrics Visualization

## 1. Architectural Overview

In production backend engineering, collecting telemetry (metrics, logs, traces) is only half the battle. Without visual synthesis, raw counters and histograms remain unparsed numbers in a time-series database. When an incident occurs or an SLA threshold is breached, engineers cannot spend critical minutes writing ad-hoc aggregation queries.

**Declarative Grafana Dashboards as Code** solves this by treating observability dashboards as version-controlled, automated, and immutable code assets. Instead of manually clicking buttons in the Grafana UI (which causes configuration drift and un-reproducible states across staging and production), the entire dashboard model is authored in declarative JSON conforming to Grafana Schema Version 38.

```
+-----------------------------------------------------------------------------------------+
|                               FASTAPI APPLICATION RUNTIME                               |
|                                                                                         |
|  [ Inbound Requests ] ---> [ CustomMiddleware ] ---> [ Handlers / DB / Services ]       |
|                                     |                                                   |
|                        http_requests_total (Counter)                                    |
|                        http_request_duration_seconds (Histogram)                        |
|                        http_requests_in_flight (Gauge)                                  |
|                        active_database_connections (Gauge)                              |
|                                     |                                                   |
|                        [ GET /metrics Endpoint ]                                        |
+-------------------------------------|---------------------------------------------------+
                                      |
                               (Periodic Scrape 15s)
                                      v
                        +---------------------------+
                        |  Prometheus TSDB Engine   |
                        +---------------------------+
                                      ^
                                      | PromQL Queries
                                      | (rate, histogram_quantile, topk)
                                      v
+-----------------------------------------------------------------------------------------+
|                               GRAFANA OBSERVABILITY LAYER                               |
|                                                                                         |
|   Declarative Model: app/core/observability/dashboards/fastapi_golden_signals.json       |
|   Provisioned via CI/CD / GitOps / Automated Import                                     |
|                                                                                         |
|   +--------------------------+   +---------------------------+                          |
|   | 1. Traffic (reqps)       |   | 2. Error Rate & SLA (%)   |                          |
|   | sum(rate(...[1m]))       |   | sum(5xx)/sum(total)*100   |                          |
|   +--------------------------+   +---------------------------+                          |
|   +----------------------------------------------------------+                          |
|   | 3. Latency Percentiles (P50, P95, P99 ms)                |                          |
|   | histogram_quantile(0.99, sum(rate(..._bucket[5m])) by(le))                         |
|   +----------------------------------------------------------+                          |
|   +--------------------------+   +---------------------------+                          |
|   | 4. Concurrency / InFlight|   | 5. Top 5 Slow Endpoints   |                          |
|   | http_requests_in_flight  |   | topk(5, rate(sum)/rate(c))|                          |
|   +--------------------------+   +---------------------------+                          |
+-----------------------------------------------------------------------------------------+
```

---

## 2. The 4 Golden Signals & PromQL Formulations

Our Grafana dashboard explicitly maps to Google SRE's **4 Golden Signals**:

### Signal 1: Traffic (Request Rate)
- **Panel Type**: Time-Series / Stat
- **PromQL**:
  ```promql
  sum(rate(http_requests_total[1m]))
  ```
- **Unit**: `reqps` (Requests per second).
- **Lookback Window**: `[1m]` captures rapid traffic spikes without introducing artificial smoothing.

### Signal 2: Errors (5xx Rate & SLA Compliance)
- **Panel Type**: Gauge with SLA Thresholds
- **PromQL**:
  ```promql
  (sum(rate(http_requests_total{status_code=~"5.."}[1m])) or vector(0)) / sum(rate(http_requests_total[1m])) * 100
  ```
- **Thresholds**:
  - Green (Normal): `< 0.1%` (99.9% Availability SLA)
  - Yellow (Warning): `0.1% - 1.0%`
  - Red (Critical Incident): `> 1.0%`

### Signal 3: Latency (P50, P95, P99 Percentiles)
- **Panel Type**: Multi-Series Graph
- **PromQL**:
  - **P50 (Median)**:
    ```promql
    histogram_quantile(0.50, sum(rate(http_request_duration_seconds_bucket[5m])) by (le))
    ```
  - **P95 (Tail Latency)**:
    ```promql
    histogram_quantile(0.95, sum(rate(http_request_duration_seconds_bucket[5m])) by (le))
    ```
  - **P99 (SLA Limit)**:
    ```promql
    histogram_quantile(0.99, sum(rate(http_request_duration_seconds_bucket[5m])) by (le))
    ```
- **Unit**: `s` (rendered with millisecond resolution).

### Signal 4: Saturation (Concurrency & Resource Utilization)
- **Panel 4A (ASGI Concurrency)**:
  ```promql
  http_requests_in_flight
  ```
- **Panel 4B (DB Connection Pool Utilization)**:
  ```promql
  active_database_connections
  ```

---

## 3. Sub-5ms O(N) Dashboard Validation Engine

To prevent broken dashboards from entering production pipelines, `DashboardService` implements automated structural and PromQL syntax validation:

1. **Schema Check**: Enforces `schemaVersion >= 36` and validates root dictionary structure.
2. **Panel ID Uniqueness**: Uses a hash set (`set[int]`) in $\mathcal{O}(1)$ time per panel to ensure zero ID collisions.
3. **PromQL Bracket Matching**: Verifies balanced parentheses `()` and valid time-range lookback expressions (`[1m]`, `[5m]`).
4. **Golden Signal Coverage**: Checks that all 4 signals (traffic, error rate, latency percentiles, and concurrency) are covered by the registered targets.

---

## 4. Production API Endpoints

- `GET /observability/dashboards/golden-signals`: Returns the declarative Grafana Dashboard JSON for automated provisioning or manual import.
- `GET /observability/dashboards/validate`: Executes automated schema validation and returns performance metrics (`validation_time_ms < 5ms`).
