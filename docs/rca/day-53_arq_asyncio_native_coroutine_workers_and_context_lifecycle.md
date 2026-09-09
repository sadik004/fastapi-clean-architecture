# RCA: ARQ Asyncio-Native Coroutine Workers, Worker Context Lifespan & Non-Blocking Queue Dispatch

- **Trigger**: Day 53 architectural evolution — Transitioning from heavyweight multi-process workers (Celery) to lightweight asyncio-native coroutine workers (ARQ) for high-throughput I/O-bound tasks, resolving worker context lifecycle management, socket churn, and strict static analysis warnings (Ruff S110).
- **Faulty Code / Pattern**:
  ```python
  # 1. Anti-Pattern: Heavy multi-process Celery worker used for non-blocking I/O webhooks/push notifications
  @celery_app.task
  def send_webhook(url: str, payload: dict):
      # Consumes 150MB+ RAM per worker process; blocks execution thread on network I/O
      requests.post(url, json=payload, timeout=10)

  # 2. Anti-Pattern: Instantiating a new HTTP client on every job execution (TCP/TLS socket exhaustion)
  async def sync_webhook_notification(ctx: dict, webhook_url: str, event_type: str, payload: dict):
      async with httpx.AsyncClient() as client:  # High TCP handshake latency & socket churn!
          response = await client.post(webhook_url, json=payload)
          return response.status_code

  # 3. Anti-Pattern: Silent exception suppression in job result parsing (Ruff S110 violation)
  try:
      raw_result = await job.result(timeout=0.01)
  except Exception:
      pass  # Ruff S110: `try`-`except`-`pass` detected, consider logging the exception
  ```
- **Root Cause**:
  1. **Memory Bloat & Thread Exhaustion**: Traditional multiprocessing workers (e.g., Celery prefork) allocate 150MB–300MB RAM per worker child process. Running thousands of parallel I/O-bound tasks (HTTP outbound webhooks, FCM/APNS notifications) causes severe memory bloat and thread pool starvation.
  2. **Socket Churn & Handshake Latency**: Creating a new `httpx.AsyncClient()` inside individual task invocations forces a TLS/TCP handshake on every task execution, risking local ephemeral port exhaustion under heavy traffic.
  3. **Unchecked Exception Suppression (Ruff S110)**: Catching broad exceptions with `pass` hides deserialization bugs or connection failures during job status inspections.
- **Resolution**:
  1. **Asyncio-Native Coroutines with ARQ**: Deployed ARQ (`arq.connections.RedisSettings`), running up to 100 concurrent non-blocking coroutines per single worker process consuming $<30\text{MB}$ total RAM.
  2. **Worker Context Lifespan Injection**: Attached a singleton `httpx.AsyncClient(timeout=10.0)` to ARQ worker `ctx['http_client']` during `startup(ctx)` and gracefully drained/closed it during `shutdown(ctx)`.
  3. **Structured Warning Logging**: Replaced empty `except Exception: pass` blocks with explicit `logger.warning(...)` to maintain complete observability while preserving graceful fallback when job results are pending.
  ```python
  # Corrected implementation in app/core/arq_app.py and app/tasks/arq_tasks.py:
  async def startup(ctx: dict[str, Any]) -> None:
      logger.info("Initializing ARQ worker lifespan context")
      ctx["http_client"] = httpx.AsyncClient(timeout=10.0)

  async def shutdown(ctx: dict[str, Any]) -> None:
      logger.info("Tearing down ARQ worker lifespan context")
      client = ctx.get("http_client")
      if client and isinstance(client, httpx.AsyncClient):
          await client.aclose()

  async def sync_webhook_notification(
      ctx: dict[str, Any],
      webhook_url: str,
      event_type: str,
      payload: dict[str, Any],
  ) -> dict[str, Any]:
      client: httpx.AsyncClient | None = ctx.get("http_client")
      # Executes over reused persistent connection pool
      ...
  ```
- **Permanent Prevention Rule**:
  - Always reserve Celery for CPU-heavy or blocking synchronous jobs (PDF generation, data science, image transformations); enforce ARQ for high-volume I/O-bound coroutine workloads (webhooks, notifications).
  - Always manage expensive network clients (HTTP, DB sessions) in ARQ's `startup`/`shutdown` lifecycle hooks via `ctx` to prevent TCP socket exhaustion.
  - Never use bare `try...except Exception: pass` when inspecting asynchronous job states; always log warnings with context (`job_id`).
