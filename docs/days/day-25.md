# Day 25: Sliding Window Log Algorithm for In-Memory Request Rate Limiting & Zero-Leak Monitoring

**Date**: 2026-09-08  
**Role**: Junior Apprentice Backend Engineer  
**Lead Architect & Mentor**: User

---

## 1. Concepts Covered Today
- **The Fixed-Window Boundary Burst Defect**:
  - Analyzed why fixed-window rate limiters fail under production traffic:
    - Fixed windows reset counters at rigid minute/hour boundaries (e.g. 12:00:00, 12:01:00).
    - A malicious or bursty client can send 100% of their limit in the last 100ms of Window 1 and another 100% in the first 100ms of Window 2.
    - This creates a **2x traffic spike** across a 200ms sub-interval, crashing downstream microservices and starving database connection pools.
  - Implemented the **Sliding Window Log Algorithm** in `app/core/dsa/sliding_window.py`:
    - Evaluates request velocity across a continuously rolling time frame $[now - window\_seconds, now]$.
    - Guarantees that at no point in continuous time can a client exceed `max_requests`, perfectly flattening traffic spikes.
- **Slotted In-Memory Architecture (`SlidingWindowLog`)**:
  - Enforced `__slots__ = ('window_seconds', 'max_requests', '_store', '_last_seen', '_lock')`:
    - Completely eliminated per-instance dynamic `__dict__` overhead.
    - Prevents arbitrary monkeypatching and minimizes memory footprint under millions of requests.
  - Utilized Python's `collections.deque` for timestamp storage:
    - `append(now)` operates in strict $\mathcal{O}(1)$ time.
    - `popleft()` stale timestamps from the front in amortized $\mathcal{O}(1)$ time per request.
- **Zero-Leak Idle Client Eviction (Memory Sweeper)**:
  - Addressed the unbounded dictionary growth trap caused by rotating client IPs and ephemeral guest sessions.
  - Implemented `evict_idle_clients(idle_seconds, now)`:
    - Scans `_last_seen` timestamps and deletes buckets where $now - last\_seen > idle\_seconds$.
    - Returns the number of purged clients, ensuring RAM consumption is proportional to *active concurrent clients*, NOT cumulative historical clients.
- **FastAPI Dependency Guard & Telemetry Endpoints**:
  - Implemented reusable dependency `check_sliding_window_rate_limit(window, limit)` in `app/core/dependencies.py`:
    - Extracts client identifier (`X-Forwarded-For` or `request.client.host`).
    - Enforces rate limits and returns client ID upon success.
    - When limit is breached, raises RFC-compliant `HTTPException(429, detail="Too Many Requests: Rate limit exceeded", headers={"Retry-After": str(retry_seconds)})`.
  - Created `app/routers/metrics_router.py` mounted at `/metrics`:
    - `GET /metrics/rate-limiter`: Returns real-time metrics (`window_seconds`, `max_requests`, `active_clients`, `total_tracked_requests`, `estimated_memory_bytes`) in $\mathcal{O}(1)$ time.
    - `GET /metrics/rate-limiter/test-protected`: Rate-limited endpoint protected by sliding window dependency.
    - `POST /metrics/rate-limiter/evict-idle`: Operational endpoint to trigger manual or cron-based idle client sweeps.

---

## 2. DSA Time & Space Complexity Enforced
- **Enqueue & Check (`record_and_check`)**:
  - Stale timestamp eviction (`popleft()`): Amortized $\mathcal{O}(1)$ time per request (each timestamp is enqueued once and dequeued once).
  - Insertion (`append()`): Strictly $\mathcal{O}(1)$ time.
  - Overall time complexity: $\mathcal{O}(1)$ amortized.
- **Retry-After Calculation**: Strictly $\mathcal{O}(1)$ time accessing oldest queue element `queue[0]`.
- **Idle Sweep (`evict_idle_clients`)**: $\mathcal{O}(C)$ time where $C$ is the number of active clients.
- **Space Complexity**:
  - Per client: strictly bounded to $\mathcal{O}(K)$ where $K = max\_requests$. Max storage per client is $K \times 8$ bytes for 64-bit float timestamps.
  - Total space: strictly bounded by active clients via periodic idle sweeps, eliminating memory leaks.

---

## 3. Summary of Test Results & Quality Gates
- **Pytest Suite**: **322 passed** in 23.54s (`100%` pass rate across 40 test modules).
  - `tests/test_sliding_window_monitoring.py`: 8 comprehensive tests passing:
    1. `test_slotted_memory_invariants`: Verifies `hasattr(SlidingWindowLog, '__dict__') is False` and strict slot containment.
    2. `test_rate_limit_enforcement_under_quota`: Proves 5 allowed requests followed by 6th rejection with exact `Retry-After`.
    3. `test_boundary_spike_defect_prevention`: Proves rejection of requests at $T=1.1s$ following requests at $T=0.8s$, demonstrating sliding window immunity to boundary burst defects.
    4. `test_rolling_window_timestamp_expiration`: Verifies that advancing time past window expiration accurately drops stale entries and restores quota.
    5. `test_zero_leak_idle_client_memory_eviction`: Simulates 1,000 ephemeral clients, asserts 100% purged upon idle sweep, resetting active client count to 0.
    6. `test_selective_idle_client_eviction`: Verifies selective pruning preserving active clients.
    7. `test_sliding_window_scaling_benchmark`: Benchmarks 10,000 operations across 50 clients, completing in **< 15ms** (well under 60ms threshold).
    8. `test_api_endpoint_rate_limiting_and_telemetry`: Verifies HTTP 200 OK under quota, HTTP 429 Too Many Requests with `Retry-After` header when exceeded, and `GET /metrics/rate-limiter` telemetry.
- **Strict Type Checking (`mypy --strict app tests alembic`)**:
  - `Success: no issues found in 69 source files`.
- **Linter & Formatting (`ruff check app tests alembic`)**:
  - `All checks passed!`.
