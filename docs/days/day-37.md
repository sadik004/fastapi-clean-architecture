# Day 37: Distributed Sliding Window Log Rate Limiter using Redis ZSET & Atomic Pipelines

## 1. Overview & Architectural Objectives
In distributed microservice architectures running multiple horizontally scaled application pods (e.g., Kubernetes replicas or Gunicorn workers), in-memory rate limiters (such as fixed-window counters or token buckets held in process memory) suffer from **multi-node synchronization blindness**. An attacker or high-volume client can route traffic across $K$ distinct replicas, multiplying their allowed throughput by $K\times$.

Furthermore, naive **fixed-window** rate limiters suffer from the critical **boundary burst defect**: a client can exhaust their quota in the last second of window $W_1$ and immediately exhaust another quota in the first second of window $W_2$, achieving a $2\times$ burst across window boundaries.

Day 37 engineers a production-grade **Distributed Sliding Window Log Rate Limiter** using **Redis Sorted Sets (ZSET)** and **Atomic Pipelines**, delivering:
1. **Global Cluster Synchronization**: All application pods share a single source of truth in Redis.
2. **Continuous Rolling Horizon**: Zero boundary burst vulnerability. The sliding window evaluates requests strictly across $[T - W, T]$.
3. **Atomic Transactional Multi-Command Pipeline**: Pruning expired logs, tentative insertion, cardinality check, and TTL refresh are executed in a single atomic roundtrip (`MULTI/EXEC`), defeating race conditions under high concurrency.
4. **RFC 6585 / RFC 7231 Compliance**: Standard HTTP 429 responses with accurate `Retry-After`, `X-RateLimit-Limit`, and `X-RateLimit-Remaining` headers.

---

## 2. Core Components & Implementation

### 2.1 `RateLimiterService` (`app/services/rate_limiter_service.py`)
- **Key Schema**: `ratelimit:{client_id}`
- **ZSET Score**: Floating-point Unix timestamp ($T$).
- **ZSET Member**: Unique request token `f"{T}:{uuid4().hex[:8]}"` to guarantee that concurrent requests at identical microsecond timestamps do not collide.
- **Atomic Pipeline Operations**:
  1. `pipe.zremrangebyscore(redis_key, 0, f"({current_time - window_seconds}")`: Prunes entries strictly older than the rolling window (open upper bound).
  2. `pipe.zadd(redis_key, {member: current_time})`: Tentatively records current request timestamp.
  3. `pipe.zcard(redis_key)`: Retrieves exact active cardinality within the window.
  4. `pipe.zrange(redis_key, 0, 0, withscores=True)`: Retrieves the oldest active timestamp to compute dynamic `Retry-After`.
  5. `pipe.expire(redis_key, int(window_seconds) + 2)`: Refreshes key TTL so idle keys are automatically reclaimed by Redis.
- **Rollback on Limit Breach**: If `current_count > limit`, `await redis.zrem(redis_key, member)` rolls back the tentative entry so dropped requests do not consume future quota.

### 2.2 Declarative `RateLimitGuard` (`app/core/dependencies.py`)
- FastAPI callable class dependency: `RateLimitGuard(limit: int, window_seconds: float, scope: str = "default")`.
- **Client Identification Cascade**:
  1. `X-API-Key` header (authenticated client tokens).
  2. First IP from `X-Forwarded-For` header (reverse proxy / load balancer).
  3. Direct socket `request.client.host` (fallback).
- Raises `HTTPException(status_code=429)` with RFC headers:
  - `Retry-After`: Integer seconds until oldest entry expires.
  - `X-RateLimit-Limit`: Maximum allowed requests.
  - `X-RateLimit-Remaining`: Remaining allowed requests in window (`max(0, limit - current_count)`).

### 2.3 Diagnostic Endpoints & Routing
- Root-level test endpoint: `GET /test-rate-limit/distributed` guarded by `RateLimitGuard(limit=5, window_seconds=10.0, scope="test")`.
- Live monitoring endpoint: `GET /metrics/rate-limit/{client_id}` returning active request count, key TTL, window seconds, and oldest/newest timestamps.

---

## 3. Algorithmic Complexity

| Operation | DSA Primitive | Time Complexity | Space Complexity |
| :--- | :--- | :--- | :--- |
| Prune Expired Logs | Redis SkipList (`ZREMRANGEBYSCORE`) | $\mathcal{O}(\log N + M)$ | $\mathcal{O}(1)$ |
| Tentative Log Insertion | Redis SkipList + Dict (`ZADD`) | $\mathcal{O}(\log N)$ | $\mathcal{O}(1)$ |
| Cardinality Count | Redis Sorted Set metadata (`ZCARD`) | $\mathcal{O}(1)$ | $\mathcal{O}(1)$ |
| Oldest Element Inspection | Redis SkipList head (`ZRANGE 0 0`) | $\mathcal{O}(\log N)$ | $\mathcal{O}(1)$ |
| Key Expiration Refresh | Redis Timer (`EXPIRE`) | $\mathcal{O}(1)$ | $\mathcal{O}(1)$ |
| **Total Pipeline Request** | Single atomic Redis roundtrip | $\mathcal{O}(\log N + M)$ | $\mathcal{O}(N)$ per client |

*Where $N$ is the number of requests in the sliding window and $M$ is the number of expired requests pruned.*

---

## 4. Verification & Test Suite

The test suite in `tests/test_redis_zset_rate_limiter.py` verifies 5 critical requirements:
1. `test_continuous_rolling_expiration`: Simulates time advancement ($T=100.0, 100.5, 101.0, 102.0, 105.5$) to prove smooth rolling expiration.
2. `test_boundary_burst_defect_defeat`: Demonstrates that requests sent across bucket boundaries (e.g., $T=9.0$ and $T=10.6$) cannot exceed the rate limit.
3. `test_concurrent_race_condition_defeat`: Fires 20 concurrent requests simultaneously via `asyncio.gather` against a limit of 10. Exactly 10 succeed and 10 fail.
4. `test_quota_enforcement_and_retry_after_headers`: Verifies HTTP 200 on allowed requests and HTTP 429 with RFC headers (`Retry-After`, `X-RateLimit-Limit`, `X-RateLimit-Remaining`) when exceeded.
5. `test_live_rate_limit_metrics_endpoint`: Validates observability probe at `GET /metrics/rate-limit/{client_id}`.
