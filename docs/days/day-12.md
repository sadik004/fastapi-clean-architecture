# Day 12: Non-Blocking vs Blocking Execution (Preventing Event Loop Starvation with asyncio.to_thread)

**Date**: 2026-09-08  
**Role**: Junior Apprentice Backend Engineer  
**Lead Architect & Mentor**: User

---

## 1. Concepts Covered Today
- **Event Loop Starvation Mechanics**:
  - Python's `asyncio` event loop executes on a single operating system thread.
  - While I/O-bound operations yield execution back to the loop via `await`, CPU-bound computations (such as cryptographic key derivations, large dataset hashing, or image compression) do not yield control.
  - Running synchronous CPU-heavy code directly on the event loop starves all concurrent requests, causing health checks and API endpoints to stall or timeout.
- **Worker Thread Offloading via `asyncio.to_thread`**:
  - Python 3.9+ provides `asyncio.to_thread(func, *args, **kwargs)`, which dispatches synchronous blocking functions to Python's default `concurrent.futures.ThreadPoolExecutor`.
  - While worker threads execute CPU-bound work, the main OS thread remains completely free to run the asyncio event loop, processing incoming HTTP requests without delay.
- **Production-Grade CPU-Bound Password Hashing (`app/core/security.py`)**:
  - Implemented OWASP-recommended key derivation using `hashlib.pbkdf2_hmac("sha256", password, salt, 100_000)`.
  - Generated unique 16-byte cryptographically secure salts via `secrets.token_bytes()`.
  - Implemented constant-time verification using `hmac.compare_digest` to eliminate side-channel timing attacks.
  - Wrapped both operations in non-blocking async functions `get_password_hash` and `verify_password` powered by `asyncio.to_thread`.
- **Heavy Analytical Report Generation Endpoint (`POST /users/{user_id}/export-report`)**:
  - Simulated compute-intensive dataset analysis (iterative cryptographic checksum calculation over 50,000 synthetic records) in `UserService._sync_compute_heavy_report`.
  - Offloaded calculation via `await asyncio.to_thread(self._sync_compute_heavy_report, user.id, user.username)`.
  - Exposed via `POST /users/{user_id}/export-report` projecting to `UserReportResponse`.
- **High-Responsiveness Health Check (`GET /health`)**:
  - Implemented `GET /health` in `app/main.py` as an `async def` endpoint executing directly on the main event loop.
  - Verified that `/health` maintains low latency (< 25ms) under heavy concurrent CPU work dispatched to worker threads.

---

## 2. Architectural Decisions Made
- **Strict Separation of Compute and Orchestration**:
  - Synchronous computation functions (`_sync_hash_password`, `_sync_verify_password`, `_sync_compute_heavy_report`) are pure, thread-safe functions that take primitive inputs and perform zero unprotected mutations on shared repository memory.
  - Thread dispatching (`asyncio.to_thread`) is confined strictly to the Security and Service layers (`app/core/security.py`, `app/services/user_service.py`).
  - Routers remain clean HTTP dispatchers that simply `await` the high-level service coroutine.
- **Non-Breaking Schema Evolution**:
  - Updated `GET /health` to return `{"status": "healthy", "timestamp": "..."}`.
  - Updated existing integration tests in `test_user.py` to assert both `status` and `timestamp` presence, maintaining full backward compatibility.

---

## 3. DSA Time & Space Complexity Enforced
- **Password Key Derivation**:
  - Time Complexity: $\mathcal{O}(K \cdot L)$ where $K = 100,000$ iterations and $L$ is password length.
  - Latency: ~20–40ms, executed off the main event loop in a background thread.
- **Heavy Dataset Checksum**:
  - Time Complexity: $\mathcal{O}(N)$ where $N = 50,000$ records hashed.
  - Latency: ~30–50ms, executed off the main event loop in a background thread.
- **Event Loop Health Check Response**:
  - Time Complexity: Strictly $\mathcal{O}(1)$ time and memory.
  - Concurrency Scaling: $\approx 1\text{--}4\text{ms}$ per request on the main event loop even while background threads are computing PBKDF2 hashes or report checksums.

---

## 4. Summary of Test Results & Quality Gates
- **Pytest**: 235 passed in 5.33s (`100%` pass rate across 27 test modules).
  - `tests/test_blocking_vs_nonblocking.py`: 5 new tests passing:
    1. `test_password_hash_and_verification`: Salt uniqueness, valid verification, wrong password rejection, malformed hash safety.
    2. `test_user_service_authentication_flow`: Success with valid credentials, failure with wrong password, failure with unknown user.
    3. `test_export_user_report_endpoint_success`: HTTP 200, 50k records processed, 64-char SHA-256 checksum.
    4. `test_export_user_report_not_found`: HTTP 404 for non-existent user.
    5. `test_event_loop_not_starved_during_heavy_cpu_work`: Concurrently executed heavy export and 5 health check requests via `httpx.AsyncClient`; confirmed all health checks resolved with low latency (< 25ms).
  - All 230 previous tests continue to pass with zero regressions.
- **Mypy**: `Success: no issues found in 30 source files` (`mypy --strict app tests`).
- **Ruff**: `All checks passed!` across `app/` and `tests/`.
