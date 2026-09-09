# RCA: Day 37 - Redis Sliding Window Exclusive Bound Pruning & Atomic Pipeline Race Conditions

- **Date**: 2026-09-09
- **Trigger**: Pytest assertion failure in `test_continuous_rolling_expiration` (`assert 2 == 3`) and potential race condition under high concurrency.
- **Faulty Code / Pattern**:
  ```python
  # Initial pruning logic in RateLimiterService:
  pipe.zremrangebyscore(redis_key, 0, current_time - window_seconds)
  ```
- **Root Cause**:
  1. In Redis, `ZREMRANGEBYSCORE key min max` operates on an inclusive interval `[min, max]` by default.
  2. In a sliding window log where a request arrived at timestamp $T_1 = 100.5$ and the current request arrives at $T_2 = 105.5$ with a window of $5.0$ seconds:
     $$\text{threshold} = 105.5 - 5.0 = 100.5$$
     Because the interval was inclusive, the entry at $100.5$ had $\text{score} \le 100.5$, causing it to be pruned immediately! However, an entry at $100.5$ is exactly $5.0$ seconds old at $105.5$ and is still validly within the rolling window $(100.5, 105.5]$. This led to an unexpected count of 2 instead of 3.
  3. Furthermore, in non-atomic sliding window implementations, checking `zcard` before calling `zadd` creates a severe race condition under concurrent requests. In our tests with 20 concurrent requests, a non-atomic approach allowed all 20 requests through despite a limit of 10.
- **Resolution**:
  1. Enforced Redis open-interval syntax (`f"({current_time - window_seconds}"`) for `ZREMRANGEBYSCORE`:
     ```python
     pipe.zremrangebyscore(redis_key, 0, f"({current_time - window_seconds}")
     pipe.zadd(redis_key, {member: current_time})
     pipe.zcard(redis_key)
     pipe.zrange(redis_key, 0, 0, withscores=True)
     pipe.expire(redis_key, int(window_seconds) + 2)
     ```
     This strictly deletes only timestamps older than the window, preserving boundary elements accurately.
  2. Executed all sliding window operations (prune, add, count, inspect, expire) within a single atomic transactional pipeline (`MULTI/EXEC`), followed by an immediate `zrem` rollback if the active count exceeds the quota. This successfully defeated the race condition, guaranteeing that out of 20 concurrent requests against a limit of 10, exactly 10 are allowed and exactly 10 are rejected.
- **Permanent Prevention Rule**:
  1. Always use exclusive upper bounds `f"({now - window}"` in Redis `ZREMRANGEBYSCORE` for sliding window logs so boundary-valid requests are not prematurely evicted.
  2. Always combine pruning, logging, counting, and TTL refresh in an atomic pipeline to defeat concurrency race conditions.
  3. Always rollback rejected requests from the ZSET via `ZREM` so blocked calls do not exhaust legitimate future client quota.
