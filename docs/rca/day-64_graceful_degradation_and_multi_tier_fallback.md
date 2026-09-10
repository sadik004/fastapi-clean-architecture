# Root Cause Analysis (RCA): Day 64 - Auxiliary Service Failures & Multi-Tier Graceful Degradation

## 1. Executive Summary

- **Incident Classification**: Resilience Architecture & Blast Radius Mitigation
- **Severity**: High (Critical Availability Pattern)
- **Primary Failure Mode**: Non-Critical Auxiliary Service Outages Bubbling into HTTP 500 Disruption for Core User Views
- **Component Under Analysis**: `app/core/resilience/fallback.py`, `app/services/recommendation_service.py`
- **Resolution**: Engineered a 3-tier Graceful Degradation Engine (Primary Live -> Stale Redis Cache -> Static Curated Default) guaranteeing zero 500 errors and injecting `X-Degraded-Mode` and `X-Degradation-Level` HTTP telemetry headers.

---

## 2. Problem Statement & Symptoms

When non-critical auxiliary services (e.g., machine learning product recommenders, ad engines, related content pickers) experience outages, latency spikes, or network partitions:
1. **Cascading View Rupture**: The primary route handler encounters an unhandled exception or timeout from the downstream dependency and raises an `HTTP 500 Internal Server Error`.
2. **Breach of Blast Radius Containment**: A user trying to browse a product catalog or complete a purchase encounters a completely broken interface simply because a secondary recommendation carousel failed.
3. **Cache Fragility**: In systems where fallback caching is attempted naively, transient errors connecting to or writing into the cache server cause the primary operation to crash, making the caching layer a new single point of failure.

---

## 3. Root Cause Analysis (5 Whys)

1. **Why did the user see a 500 error on the home page?**  
   Because the recommendation API endpoint raised an unhandled `ServiceUnavailableException`.
2. **Why was the recommendation service unavailable?**  
   Because the downstream AI recommendation microservice timed out under heavy load.
3. **Why did an auxiliary recommendation failure crash the entire view?**  
   Because the route handler lacked a multi-tier fallback ladder to catch downstream failures and return degraded data.
4. **Why couldn't the system simply use cached recommendations?**  
   Because the cache lookups and primary executions were coupled without progressive step-down tiers (Primary -> Stale Cache -> Static Default).
5. **Why was a static default necessary?**  
   Because if both the primary service and the Redis caching cluster are unavailable simultaneously (e.g. during a catastrophic network partition), only an in-memory static curated default can guarantee `HTTP 200 OK` with valid data.

---

## 4. Architectural Solution & Implementation

### 4.1 Three-Tier Fallback Ladder (`FallbackEngine`)
```python
async def execute_with_fallback(
    self,
    primary_func: Callable[[], Awaitable[T]],
    fallback_cache_key: str | None,
    static_default: T,
    cache_ttl: int = 3600,
    ...
) -> tuple[T, DegradationLevel]:
    # Tier 1: Primary Live Call
    try:
        data = await primary_func()
        if fallback_cache_key:
            await self._safe_cache_set(fallback_cache_key, data, cache_ttl, serialize)
        return data, DegradationLevel.PRIMARY
    except Exception as primary_exc:
        logger.warning("Primary failed. Stepping down to Tier 2 (Stale Cache)...")

    # Tier 2: Stale Redis Cache Fallback
    if fallback_cache_key:
        cached_data = await self._safe_cache_get(fallback_cache_key, deserialize)
        if cached_data is not None:
            return cached_data, DegradationLevel.STALE_CACHE

    # Tier 3: Static Curated Default
    return static_default, DegradationLevel.STATIC_DEFAULT
```

### 4.2 Silent Cache Degradation (`_safe_cache_set` & `_safe_cache_get`)
- Redis client calls are wrapped in defensive try-except blocks.
- Cache read or write errors log warnings and return `None` rather than raising exceptions, ensuring the caching tier can never break live responses.

### 4.3 HTTP Telemetry Headers
- `X-Degraded-Mode: FALSE` (for Tier 1) or `TRUE` (for Tier 2 and Tier 3).
- `X-Degradation-Level: PRIMARY | STALE_CACHE | STATIC_DEFAULT`.
- Enables synthetic monitoring tools (Datadog, Prometheus) and frontend clients to observe degradation without breaking user flows.

---

## 5. Preventative Rules & Guardrails

1. **Non-Critical Path Rule**: Any service not essential to the fundamental transactional integrity of an application (checkout, authentication) must be shielded with a fallback ladder.
2. **Zero 500 Contract**: Endpoints serving auxiliary UI components must guarantee HTTP 200 with fallback data.
3. **Read-Only Scope**: Multi-tier fallbacks must only be applied to idempotent read/query operations, never to financial transactions or state mutations.
