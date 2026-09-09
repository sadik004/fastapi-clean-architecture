# Day 53: Asyncio-Native Task Queues with ARQ (Async Redis Queue) & Coroutine Workers

**Date:** 2026-09-09  
**Topic:** Asyncio-Native Task Queues, ARQ Redis Backend, Coroutine Workers & Context Lifespan  
**Status:** ✅ Completed | 10/10 Dedicated Tests Passing | Full Suite 527/527 Passing  

---

## 🎯 Lesson Objective

Engineer an ultra-lightweight, asyncio-native background task queue architecture powered by **ARQ (Async Redis Queue)** to execute I/O-bound background coroutines (outbound webhook delivery, high-throughput push notification broadcasts, external microservice sync) with **90% less RAM overhead** than process-heavy Celery workers:
1. **ARQ Worker Settings & Lifespan Hooks (`app/core/arq_app.py`)**:
   - Configure `RedisSettings.from_dsn(Settings.redis_url)`.
   - Register `sync_webhook_notification` and `broadcast_push_notification` in `WorkerSettings.functions`.
   - Hardened settings: `max_jobs = 100`, `job_timeout = 60s`, `keep_result = 3600s`.
   - Lifespan management: `startup(ctx)` initializes a persistent `httpx.AsyncClient` in worker context; `shutdown(ctx)` drains and closes connection pools gracefully.
2. **Async I/O-Bound Task Coroutines (`app/tasks/arq_tasks.py`)**:
   - `sync_webhook_notification`: Non-blocking HTTP POST webhook transmission utilizing shared `ctx['http_client']`.
   - `broadcast_push_notification`: Asynchronous notification broadcaster fan-out returning delivery metrics and SHA-256 message digests.
3. **Service & Management Layer (`app/services/arq_service.py` & `app/routers/arq_router.py`)**:
   - `ArqService`: Pool caching, `enqueue_webhook_task`, `enqueue_broadcast_task`, and `get_job_status`.
   - Endpoints:
     - `POST /arq/jobs/webhook`: Returns HTTP 202 Accepted with correlation `job_id`.
     - `POST /arq/jobs/broadcast`: Returns HTTP 202 Accepted for bulk fan-out.
     - `GET /arq/jobs/{job_id}`: Polls live ARQ job execution status and returns result.
4. **Testing Architecture (`tests/test_arq_task_queue.py`)**:
   - Dedicated test suite with 10 passing tests verifying coroutine execution, worker settings, enqueueing, and REST endpoints.

---

## 🏗️ Celery vs ARQ: Architectural Decision Matrix

| Dimension | Celery Background Workers | ARQ (Async Redis Queue) |
|---|---|---|
| **Execution Model** | OS-level Pre-fork Process Pool (`multiprocessing`) | Python `asyncio` Single-Process Event Loop Coroutines |
| **Ideal Workload** | **CPU-bound / Synchronous** (PDF/Excel generation, image resizing, heavy financial math) | **I/O-bound / Asynchronous** (Webhooks, push notifications, third-party REST calls, email alerts) |
| **RAM Overhead per Worker** | $50\text{--}80\text{ MB}$ per process (10 workers = $\sim 800\text{ MB}$) | $< 30\text{ MB}$ total for up to 100 concurrent jobs |
| **Concurrency Ceiling** | Limited by OS process limits ($4\text{--}16$ processes per server) | Thousands of coroutines multiplexed on single event loop |
| **Connection Pooling** | Each process establishes separate Redis/DB/HTTP connections | Shared connection pools (`httpx.AsyncClient`) in worker `ctx` |
| **Dispatch Latency** | $\sim 2\text{--}5\text{ ms}$ | Sub-millisecond ($< 0.8\text{ ms}$) |

---

## 📦 Files Created & Modified

### Created
- `app/tasks/arq_tasks.py`: Implemented `sync_webhook_notification` and `broadcast_push_notification`.
- `app/core/arq_app.py`: Configured `WorkerSettings`, `get_arq_redis_settings`, and `startup`/`shutdown` lifespan hooks.
- `app/schemas/arq.py`: Pydantic DTOs `WebhookJobRequest`, `PushNotificationJobRequest`, `ArqJobEnqueueResponse`, `ArqJobStatusResponse`.
- `app/services/arq_service.py`: Service coordination for ARQ pool lifecycle, task dispatching, and status polling.
- `app/routers/arq_router.py`: REST endpoints mounted at `/arq`.
- `tests/test_arq_task_queue.py`: 10 comprehensive unit and integration tests.
- `docs/days/day-53.md`: English architectural documentation.
- `docs/days_bn/day-53.md`: 100% Bengali pedagogical guide following the 10-part framework.
- `docs/rca/day-53_arq_asyncio_native_coroutine_workers_and_context_lifecycle.md`: RCA on worker context reuse.

### Modified
- `pyproject.toml`: Added `arq` to mypy type overrides.
- `app/main.py`: Mounted `arq_router` at `/arq`.
- `ROADMAP.md`: Marked Day 53 as completed `[x]`.
- `docs/days_bn/README.md`: Appended Day 53 to table of contents.
- `docs/rca/README.md`: Appended Day 53 RCA entry.
- `.agents/skills/fastapi-production/SKILL.md`: Codified Patterns #151, #152 and Bad Patterns #122, #123.

---

## 🧪 Verification & Quality Gate Results

### 1. Static Type Analysis (`mypy`)
```bash
mypy --strict app tests alembic
Success: no issues found in 156 source files
```

### 2. Linting & Formatting (`ruff`)
```bash
ruff check app tests alembic
All checks passed!
```

### 3. Dedicated ARQ Test Suite (`pytest`)
```bash
pytest tests/test_arq_task_queue.py -v
======================== 10 passed, 1 warning in 1.26s ========================
```

### 4. Combined Background Queues Regression Suite
```bash
pytest tests/test_arq_task_queue.py tests/test_celery_task_queue.py tests/test_celery_beat_schedules.py -v
======================== 30 passed, 1 warning in 7.08s ========================
```
