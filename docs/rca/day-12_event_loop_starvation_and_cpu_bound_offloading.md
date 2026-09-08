# RCA: Day 12 Event Loop Starvation, CPU-Bound Offloading & Thread Concurrency

- **Trigger**: Concurrent request stalls and health check latency degradation during compute-intensive cryptographic hashing and analytical report generation.

---

## 1. Incident 1: Event Loop Starvation by CPU-Heavy Key Derivation (PBKDF2)

### Faulty Code / Pattern
```python
# Synchronous execution directly on the main event loop
async def register_user(self, payload: UserCreate) -> UserEntity:
    # PBKDF2 with 100,000 iterations takes ~25-35ms of 100% single-core CPU time!
    password_hash = hashlib.pbkdf2_hmac(
        "sha256",
        payload.password.encode("utf-8"),
        salt,
        100_000,
    )
    ...
```

### Root Cause
Python's `asyncio` event loop executes cooperatively on a single operating system thread. While I/O-bound coroutines yield control back to the loop via `await`, CPU-bound calculations (such as cryptographic key derivation with 100,000 iterations or large dataset hashing) do not yield control. Executing CPU-bound computation directly inside an `async def` function monopolizes the event loop thread, completely blocking all concurrent HTTP requests and causing health checks (`GET /health`) to spike in latency or time out.

### Resolution
Offload all CPU-bound operations to worker threads via `asyncio.to_thread`:
```python
# app/core/security.py
def _sync_hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    derived = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 100_000)
    return f"pbkdf2:sha256:100000${salt.hex()}${derived.hex()}"

async def get_password_hash(password: str) -> str:
    """Non-blocking password hashing offloaded to worker thread pool."""
    return await asyncio.to_thread(_sync_hash_password, password)
```

---

## 2. Incident 2: Shared State Mutation Hazards in Worker Threads

### Faulty Code / Pattern
```python
# Worker function mutating shared in-memory repository without locks
def _sync_compute_and_save(repo: InMemoryUserRepository, user: UserEntity) -> None:
    # Running inside ThreadPoolExecutor thread:
    repo._storage[user.id] = updated_entity  # Race condition! Unsynchronized dictionary mutation!
```

### Root Cause
Functions dispatched to worker threads via `asyncio.to_thread` run in separate OS threads from the default `ThreadPoolExecutor`. Mutating shared, un-synchronized in-memory state or repository dictionaries from background worker threads introduces race conditions, dictionary resizing errors (`RuntimeError: dictionary changed size during iteration`), and subtle memory corruption.

### Resolution
Enforce pure, thread-safe worker functions. Functions executed via `asyncio.to_thread` must operate exclusively on immutable local arguments or return immutable computation DTOs, leaving state persistence to the main event loop:
```python
# app/services/user_service.py
@staticmethod
def _sync_compute_heavy_report(user_id: int, username: str) -> dict[str, Any]:
    """Pure CPU-bound calculation with zero mutations of shared memory."""
    hasher = hashlib.sha256()
    for idx in range(50_000):
        chunk = f"record_{idx}:{user_id}:{username}:{idx * 31}".encode("utf-8")
        hasher.update(chunk)
    return {
        "user_id": user_id,
        "username": username,
        "processed_records": 50_000,
        "report_checksum": hasher.hexdigest(),
        "generated_at": datetime.now(timezone.utc),
    }
```

---

## 3. Preventive Rules Codified
1. **Never Compute CPU-Heavy Work on Event Loop**: Any task exceeding 5ms of CPU calculation (PBKDF2 key derivations, image resizing, large checksum loops) must be offloaded via `await asyncio.to_thread()`.
2. **Pure Worker Functions**: Functions passed to `asyncio.to_thread` must take primitive arguments, avoid shared heap mutations, and return pure DTOs.
3. **High-Responsiveness Health Checks**: Keep probes (`GET /health`) lightweight and non-blocking so orchestrators (e.g. Kubernetes) never fail liveness checks under heavy load.
