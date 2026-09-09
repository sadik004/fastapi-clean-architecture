# RCA: Day 41 - Distributed Lock Blind DEL Hijacking Hazard & Mandatory Millisecond TTL Fencing

- **Date**: 2026-09-09
- **Trigger**: High-concurrency distributed lock race hazard and cluster safety analysis during Day 41 implementation:
  1. The catastrophic "Lock Hijacking / Accidental Release" vulnerability caused by issuing an un-fenced blind `DEL` command upon job completion.
  2. The "Permanent Cluster Deadlock" vulnerability if locks are acquired without an enforced, non-zero millisecond TTL (`PX`).
  3. Linter import ordering violation (`ruff I001`) during DSA package export in `app/core/dsa/__init__.py`.

- **Faulty Code / Pattern**:
  ```python
  # FLAW 1: Blind DEL upon completion — triggers catastrophic lock hijacking
  async def release_unsafe(self) -> None:
      # If this worker's lock expired and another worker acquired it,
      # this blindly deletes the OTHER worker's valid lock!
      await self._redis.delete(f"lock:{self.name}")
  ```
  And acquiring without mandatory TTL:
  ```python
  # FLAW 2: Indefinite lock acquisition — risks permanent cluster deadlock
  await self._redis.set(f"lock:{self.name}", self.token, nx=True)  # Missing px=ttl_ms!
  ```

- **Root Cause**:
  1. **Lock Hijacking via Blind DEL**:
     In distributed systems, workers can experience unpredictable latency spikes (garbage collection pauses, database connection timeouts, slow third-party API responses). If Worker A acquires a lock with a 5000ms TTL but takes 5200ms to finish:
     - At $T = 5000\text{ms}$, Redis automatically evicts the expired key.
     - At $T = 5050\text{ms}$, Worker B detects the key is missing and successfully acquires the lock with its own token.
     - At $T = 5200\text{ms}$, Worker A finishes its job and executes `redis.delete(key)`.
     - Worker A has now deleted Worker B's active lock!
     - At $T = 5250\text{ms}$, Worker C acquires the lock while Worker B is actively mutating data in the critical section. Mutual exclusion is completely violated.
  2. **Permanent Deadlock on Node Failure**:
     If a lock is acquired without an automatic expiration TTL, and the acquiring container/pod is killed (e.g., Kubernetes OOMKilled, node host crash, uncaught SIGKILL), the key will never be removed. The entire distributed cluster remains permanently blocked from executing that job until manual Redis intervention.

- **Resolution**:
  1. **Safe Atomic Release via Canonical Lua Script**:
     Implemented an atomic Lua script that compares the caller's unique UUID4 token against the stored token before deletion:
     ```python
     _RELEASE_LUA_SCRIPT = """
     if redis.call("get", KEYS[1]) == ARGV[1] then
         return redis.call("del", KEYS[1])
     else
         return 0
     end
     """

     async def release(self) -> bool:
         if not self._acquired or not self.token:
             return False
         try:
             res: Any = await self._redis.eval(_RELEASE_LUA_SCRIPT, 1, self.key, self.token)
             return int(res) == 1
         finally:
             self._acquired = False
             self.token = None
     ```
     If Worker A's lock has expired and Worker B holds the lock, Worker A's token does not match Worker B's token. The Lua script returns `0` and does NOT delete the key.
  2. **Mandatory Millisecond TTL Enforcement**:
     Enforced `ttl_ms: int = 5000` with `max(1, ttl_ms)` and executed `SET NX PX {ttl_ms}` on every acquire operation, guaranteeing zero permanent deadlocks.
  3. **Alphabetical Import Normalization**:
     Organized imports in `app/core/dsa/__init__.py` alphabetically to satisfy `ruff I001`.

- **Permanent Prevention Rules**:
  1. **Never Issue Blind `DEL` on Distributed Locks**: Every distributed lock MUST use a unique token per acquisition and release ONLY via an atomic check-and-delete Lua script.
  2. **Always Enforce Mandatory Expiration (`PX`)**: Never expose or allow an API that creates distributed locks without a finite TTL.
  3. **Map Lock Failures to HTTP 409 Conflict**: A failure to acquire a distributed lock signals that a resource is concurrently busy; map this to domain `DistributedLockConflictException` and HTTP 409 Conflict rather than HTTP 500.
