# RCA: Day 25 - The Fixed-Window Boundary Burst Defect & Unbounded In-Memory Limiter Leak

- **Date**: 2026-09-08
- **Role**: Junior Apprentice Backend Engineer
- **Lead Architect & Mentor**: User
- **Topic**: Rate Limiting Algorithms, Sliding Window Log with `collections.deque`, and Ephemeral Client Memory Pruning

---

## 1. Trigger
High-throughput production backends face two distinct vulnerabilities when implementing naive rate limiters:
1. **The Boundary Burst Defect**: Under fixed-window algorithms, clients exploit counter resets to send 2x their allocated quota across boundary intervals, overwhelming downstream dependencies.
2. **The Ephemeral Client Memory Leak**: In-memory rate limiters that store client keys in a dictionary without active eviction leak memory indefinitely as rotating IP addresses, web scrapers, and guest sessions accumulate over time.

---

## 2. Faulty Code / Pattern

### Defect A: Fixed-Window Counter Boundary Spike
```python
# Naive fixed-window counter
class FixedWindowLimiter:
    def __init__(self, window_seconds: int = 60, limit: int = 100):
        self.window_seconds = window_seconds
        self.limit = limit
        self.counts = {}  # (client_id, window_id) -> count

    def allow(self, client_id: str) -> bool:
        window_id = int(time.time() // self.window_seconds)
        count = self.counts.get((client_id, window_id), 0)
        if count >= self.limit:
            return False
        self.counts[(client_id, window_id)] = count + 1
        return True
```
**Failure Mechanics**:
- Limit: 100 req / 60s.
- Client sends 100 requests at 11:59:59 (window $W_1$).
- At 12:00:00 (window $W_2$), the counter resets to 0.
- Client sends another 100 requests at 12:00:01.
- **Result**: The client successfully issued 200 requests within 2 seconds—a **200% burst** that crashes the application server while technically adhering to the fixed window rules.

### Defect B: Unbounded Dictionary Growth
```python
# Leaking in-memory client store
class UnboundedRateLimiter:
    def __init__(self):
        self.store = {}  # client_id -> deque of timestamps

    def check(self, client_id: str):
        # Adds client_id forever, never removing inactive keys
        if client_id not in self.store:
            self.store[client_id] = deque()
        ...
```
In internet-facing applications, IP addresses change constantly (mobile towers, NAT gateways, rotating proxies). Without an active idle sweeper, `self.store` retains millions of empty or single-use keys, eventually triggering Linux OOM killer termination.

---

## 3. Root Cause
1. **Discrete Time Bucket vs Continuous Rolling Window**: Fixed windows quantize continuous time into rigid discrete buckets. Rate limiting is inherently a velocity check across a continuous time span $\Delta t$; it requires continuous window evaluation.
2. **Missing Inactivity Lifecycle**: Memory structures tracking external entities must have an explicit expiration policy (`_last_seen` timestamp) and active garbage collection.

---

## 4. Resolution

### Solution A: Slotted Sliding Window Log with Amortized $\mathcal{O}(1)$ Eviction
```python
class SlidingWindowLog:
    __slots__ = ("window_seconds", "max_requests", "_store", "_last_seen", "_lock")

    def record_and_check(self, client_id: str, now: Optional[float] = None) -> tuple[bool, int, float]:
        now = time.time() if now is None else now
        threshold = now - self.window_seconds

        with self._lock:
            queue = self._store.get(client_id)
            if queue is None:
                queue = deque()
                self._store[client_id] = queue

            # Amortized O(1) eviction of stale timestamps
            while queue and queue[0] <= threshold:
                queue.popleft()

            if len(queue) >= self.max_requests:
                retry_after = max(0.0, (queue[0] + self.window_seconds) - now)
                return False, len(queue), retry_after

            queue.append(now)
            self._last_seen[client_id] = now
            return True, len(queue), 0.0
```
By evaluating $queue[0] > (now - window\_seconds)$, the limiter evaluates the exact rolling horizon. A client attempting to send requests at $T=1.1s$ after bursting at $T=0.8s$ is immediately rejected because the rolling window $[0.1s, 1.1s]$ retains the active requests.

### Solution B: Zero-Leak Idle Client Sweeps
```python
def evict_idle_clients(self, idle_seconds: float, now: Optional[float] = None) -> int:
    now = time.time() if now is None else now
    threshold = now - idle_seconds

    with self._lock:
        stale_keys = [k for k, last in self._last_seen.items() if last < threshold]
        for k in stale_keys:
            self._store.pop(k, None)
            self._last_seen.pop(k, None)
        return len(stale_keys)
```
Purging keys with no activity for $> idle\_seconds$ resets memory to zero when traffic subsides, bounding RAM consumption to concurrent active users.

---

## 5. Prevention Rules
1. **Always Use Continuous Rolling Windows for Critical Endpoints**: Never use fixed-window counters for financial, authentication, or high-cost compute endpoints vulnerable to boundary bursts.
2. **Use `collections.deque` for FIFO Timestamp Queues**: Python's `deque` provides $\mathcal{O}(1)$ `append` and $\mathcal{O}(1)$ `popleft()`, ensuring eviction is amortized $\mathcal{O}(1)$ per request.
3. **Always Couple In-Memory Maps with Idle Eviction**: In-memory state keyed by arbitrary client identifiers must always expose an `evict_idle` mechanism to protect the process from memory exhaustion.
