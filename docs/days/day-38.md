# Day 38: Token Bucket Rate Limiting Architecture with Atomic Redis Lua Scripts

## 1. Overview & Architectural Objectives
While the **Sliding Window Log** algorithm (Day 37) provides precision by tracking individual request timestamps, it incurs an $\mathcal{O}(N)$ space penalty per client because every single request generates an entry in a Redis Sorted Set (ZSET). Under high-volume enterprise traffic (e.g. 100,000 active clients sending thousands of requests per second), storing and pruning millions of timestamp entries consumes hundreds of megabytes of Redis memory.

Furthermore, naive client-side token bucket implementations (fetching tokens via `GET` and saving via `SET`) suffer from fatal **race conditions** under concurrency: multiple application pods concurrently read the same token balance, decrement in parallel, and overwrite each other, allowing catastrophic burst over-granting.

Day 38 engineers the industry-standard **Token Bucket Rate Limiter** (utilized by AWS API Gateway, Stripe, and GitHub) powered by **Atomic Redis Lua Scripts** (`EVALSHA`), achieving:
1. **Strict $\mathcal{O}(1)$ Space Efficiency**: Exactly 2 floating-point values (`tokens` and `last_updated`) are stored per client in a Redis Hash, consuming $< 100$ bytes per client regardless of traffic volume (a $> 90\%$ memory saving over ZSET logs).
2. **Strict $\mathcal{O}(1)$ Single-Threaded Atomicity**: The entire mathematical refill, token deduction, TTL refresh, and rejection computation executes within an atomic Redis Lua script on Redis's single execution thread, defeating all race conditions under arbitrary concurrency.
3. **Burst-Friendly Traffic Shaping**: Allows legitimate traffic bursts up to the bucket's maximum capacity ($C$) while strictly capping sustained consumption at the refill rate ($r$ tokens/second).
4. **Pre-compiled `EVALSHA` Performance with `NOSCRIPT` Fallback**: Loads script bytecode into Redis once via `SCRIPT LOAD` and invokes it via 40-character SHA digests, eliminating network overhead of re-transmitting Lua script text.
5. **RFC 6585 & RFC 7231 Compliance**: Emits standard HTTP 429 responses with accurate `Retry-After`, `X-RateLimit-Limit`, and `X-RateLimit-Remaining` headers.

---

## 2. Mathematical Mechanics of Token Bucket

Let:
- $C$: Bucket Capacity (maximum allowed burst).
- $r$: Refill Rate (tokens replenished per second).
- $k$: Requested Tokens (cost of current operation, default $1.0$).
- $T_{\text{now}}$: Current Unix timestamp in seconds.
- $T_{\text{last}}$: Timestamp of the previous evaluation.
- $B_{\text{stored}}$: Token balance saved from the previous evaluation.

### Step 1: Elapsed Time & Refill Calculation
$$\Delta t = \max(0, T_{\text{now}} - T_{\text{last}})$$
$$B_{\text{refilled}} = \min(C, B_{\text{stored}} + \Delta t \times r)$$

### Step 2: Evaluation & State Mutation
- **Case 1: Quota Available ($B_{\text{refilled}} \ge k$)**
  - Deduct tokens: $B_{\text{new}} = B_{\text{refilled}} - k$
  - Save: `HSET key tokens B_new last_updated T_now`
  - Set key TTL: $\text{TTL} = \lceil C / r \rceil + 2$
  - Return: `{1, B_new, 0}` $\rightarrow$ **Allowed (HTTP 200)**

- **Case 2: Quota Exhausted ($B_{\text{refilled}} < k$)**
  - Calculate required wait time:
    $$\text{Retry-After} = \frac{k - B_{\text{refilled}}}{r}$$
  - Save refilled state: `HSET key tokens B_refilled last_updated T_now`
  - Set key TTL: $\text{TTL} = \lceil C / r \rceil + 2$
  - Return: `{0, B_refilled, Retry-After}` $\rightarrow$ **Rejected (HTTP 429)**

---

## 3. Core Components & Implementation

### 3.1 Pure Redis Lua Script (`app/core/dsa/token_bucket.lua`)
- Encapsulates state retrieval (`HMGET`), delta calculation, token clamping, state persistence (`HMSET`), and key expiration (`EXPIRE`) in a single atomic transaction.

### 3.2 Service Layer (`RateLimiterService` in `app/services/rate_limiter_service.py`)
- `check_token_bucket(key, capacity, refill_rate, requested=1.0, now=None)`:
  - Dispatches `evalsha(sha, 1, redis_key, capacity, refill_rate, requested, now)`.
  - Transparently catches `NOSCRIPT` exceptions (e.g. after Redis restarts or script flushes), re-runs `script_load`, and re-executes `evalsha`.
- `get_token_bucket_status(key, capacity, refill_rate, now=None)`:
  - Read-only probe for telemetry endpoints, dynamically computing effective tokens based on current time delta.

### 3.3 Declarative FastAPI Dependency (`TokenBucketGuard` in `app/core/dependencies.py`)
- Callable dependency class: `TokenBucketGuard(capacity=10.0, refill_rate=2.0, requested=1.0, scope="default")`.
- Resolves client identifier: `X-API-Key` $\rightarrow$ `X-Forwarded-For` $\rightarrow$ Socket host.
- Raises `HTTPException(429)` with headers:
  - `Retry-After`: $\lceil \text{retry\_after} \rceil$
  - `X-RateLimit-Limit`: $\text{int}(\text{capacity})$
  - `X-RateLimit-Remaining`: $\text{int}(\text{remaining\_tokens})$

---

## 4. Algorithmic Complexity Comparison

| Algorithm | Redis Data Structure | Time Complexity | Space Complexity Per Client | Burst Friendly? | Multi-Node Safe? |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Fixed Window Counter** | String (`INCR`) | $\mathcal{O}(1)$ | $\mathcal{O}(1)$ (< 50 bytes) | No (2x Boundary Burst) | Yes |
| **Sliding Window Log** | Sorted Set (`ZSET`) | $\mathcal{O}(\log N + M)$ | $\mathcal{O}(N)$ (~1–10 KB) | Yes (Smooth) | Yes |
| **Token Bucket (Atomic Lua)** | Hash (`HSET`) | $\mathcal{O}(1)$ | $\mathcal{O}(1)$ (< 100 bytes) | **Yes (Up to Capacity $C$)** | **Yes (Atomic Lua)** |

---

## 5. Verification & Test Suite

The test suite in `tests/test_token_bucket_rate_limiter.py` verifies:
1. `test_burst_allowance_service`: Verifies immediate consumption of full capacity ($C=5$) without throttling.
2. `test_throttling_after_burst`: Confirms 6th immediate request is rejected with accurate `Retry-After: 1.0s`.
3. `test_token_refill_mechanism`: Proves advancing simulated time by 2.0s allows exactly 2 new requests.
4. `test_high_concurrency_atomic_lua_defeat`: Fires 30 concurrent async requests against capacity 10 with 0 refill; **exactly 10 succeed, exactly 20 are rejected**, proving absolute zero-race-condition Lua atomicity.
5. `test_http_token_bucket_endpoint_and_headers`: Validates HTTP 200 on quota and HTTP 429 with RFC headers on exceedance.
6. `test_metrics_token_bucket_endpoint`: Validates `/metrics/token-bucket/{client_id}` probe data.
