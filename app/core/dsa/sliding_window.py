"""Sliding Window Log Algorithm for In-Memory Request Rate Limiting & Zero-Leak Monitoring."""

import sys
import threading
import time
from collections import deque
from typing import Any


class SlidingWindowLog:
    """In-memory Sliding Window Log rate limiter with continuous time horizon evaluation.

    Eliminates the "Boundary Spike Defect" of fixed-window limiters by evaluating request
    velocity across a continuously rolling time frame [now - window_seconds, now].
    Enforces memory bounds with slotted attributes and amortized O(1) stale timestamp eviction.
    """

    __slots__ = ("window_seconds", "max_requests", "_store", "_last_seen", "_lock")

    def __init__(self, window_seconds: float, max_requests: int) -> None:
        """Initialize the sliding window log rate limiter.

        Args:
            window_seconds: Rolling window duration in seconds.
            max_requests: Maximum allowed requests within any rolling window.
        """
        if window_seconds <= 0:
            raise ValueError("window_seconds must be strictly positive")
        if max_requests <= 0:
            raise ValueError("max_requests must be strictly positive")

        self.window_seconds: float = float(window_seconds)
        self.max_requests: int = int(max_requests)
        self._store: dict[str, deque[float]] = {}
        self._last_seen: dict[str, float] = {}
        self._lock: threading.Lock = threading.Lock()

    def record_and_check(self, client_id: str, now: float | None = None) -> tuple[bool, int, float]:
        """Record an incoming request timestamp and evaluate rate limit compliance.

        Algorithm:
        1. Window boundary: threshold = now - window_seconds.
        2. Amortized O(1) Eviction: Pop stale timestamps from queue left.
        3. Rate Check: If len(queue) >= max_requests, compute retry_after and return (False, count, retry_after).
        4. Accept: Append now to queue right, update last_seen, and return (True, count, 0.0).

        Args:
            client_id: Unique client identifier (IP address, API token, user ID).
            now: Optional explicit timestamp in seconds (defaults to time.time()).

        Returns:
            Tuple of (allowed: bool, current_request_count: int, retry_after_seconds: float).
        """
        current_time = time.time() if now is None else float(now)
        threshold = current_time - self.window_seconds

        with self._lock:
            queue = self._store.get(client_id)
            if queue is None:
                queue = deque()
                self._store[client_id] = queue

            # Amortized O(1) eviction of stale timestamps outside rolling window
            while queue and queue[0] <= threshold:
                queue.popleft()

            if len(queue) >= self.max_requests:
                # Oldest valid request timestamp dictates when the next slot will open up
                oldest_timestamp = queue[0]
                retry_after = max(0.0, (oldest_timestamp + self.window_seconds) - current_time)
                return False, len(queue), retry_after

            # Under quota: record timestamp and record client activity
            queue.append(current_time)
            self._last_seen[client_id] = current_time
            return True, len(queue), 0.0

    def evict_idle_clients(self, idle_seconds: float, now: float | None = None) -> int:
        """Purge stale client buckets inactive for longer than idle_seconds.

        Prevents unbounded dictionary memory growth under high volumes of unique,
        rotating client identifiers (e.g. ephemeral IPs or guest sessions).

        Args:
            idle_seconds: Maximum period of inactivity before eviction.
            now: Optional explicit timestamp in seconds.

        Returns:
            Number of client buckets purged.
        """
        current_time = time.time() if now is None else float(now)
        stale_threshold = current_time - idle_seconds

        with self._lock:
            stale_keys = [client_id for client_id, last_time in self._last_seen.items() if last_time < stale_threshold]
            for client_id in stale_keys:
                self._store.pop(client_id, None)
                self._last_seen.pop(client_id, None)
            return len(stale_keys)

    def active_client_count(self) -> int:
        """Return the number of currently tracked unique clients."""
        with self._lock:
            return len(self._store)

    def total_tracked_requests(self) -> int:
        """Return total active timestamps currently recorded across all clients."""
        with self._lock:
            return sum(len(q) for q in self._store.values())

    def get_client_request_count(self, client_id: str, now: float | None = None) -> int:
        """Return the number of active requests in the rolling window for a client."""
        current_time = time.time() if now is None else float(now)
        threshold = current_time - self.window_seconds

        with self._lock:
            queue = self._store.get(client_id)
            if not queue:
                return 0
            # Count elements greater than threshold
            return sum(1 for ts in queue if ts > threshold)

    def get_metrics(self) -> dict[str, Any]:
        """Compute real-time operational telemetry in O(1) to O(C) time."""
        current_time = time.time()
        with self._lock:
            active_clients = len(self._store)
            total_requests = sum(len(q) for q in self._store.values())

            # Memory footprint approximation
            dict_memory = sys.getsizeof(self._store) + sys.getsizeof(self._last_seen)
            queue_memory = sum(sys.getsizeof(q) + (len(q) * 8) for q in self._store.values())
            estimated_bytes = dict_memory + queue_memory

            return {
                "window_seconds": self.window_seconds,
                "max_requests": self.max_requests,
                "active_clients": active_clients,
                "total_tracked_requests": total_requests,
                "estimated_memory_bytes": estimated_bytes,
                "timestamp": current_time,
            }

    def reset(self) -> None:
        """Clear all client queues and timestamps from memory."""
        with self._lock:
            self._store.clear()
            self._last_seen.clear()
