# Day 52: Periodic Task Scheduling & Cron Pipelines with Celery Beat

**Date:** 2026-09-09  
**Topic:** Distributed Periodic Task Scheduling, Celery Beat Single-Leader Architecture & Cron Pipelines  
**Status:** ✅ Completed | 11/11 Dedicated Tests Passing | Full Suite 517/517 Passing  

---

## 🎯 Lesson Objective

Engineer an enterprise-grade Distributed Periodic Task Scheduling architecture powered by **Celery Beat** and **Redis Broker** to automate scheduled maintenance, nightly financial reconciliation, and recurring system jobs without clock drift or duplicate job execution:
1. **Celery Beat Schedule Configuration (`app/core/celery_app.py`)**:
   - Configure `beat_schedule` inside Celery configuration using `celery.schedules.crontab`:
     - **Nightly Financial Audit & Reconciliation**: `crontab(hour=0, minute=0)` (Daily at 00:00 UTC).
     - **Hourly Stale Session & Ephemeral Data Pruning**: `crontab(minute=0)` (Hourly at minute 0).
     - **Periodic System Health Heartbeat**: `60.0` (Every 60 seconds).
   - Configure persistence schedule shelf: `beat_schedule_filename = "celerybeat-schedule"`.
2. **Periodic Task Implementations (`app/tasks/scheduled_tasks.py`)**:
   - `nightly_reconciliation_audit()`: Ledger verification, double-entry audit summary with SHA-256 integrity hash.
   - `prune_expired_sessions_and_tokens()`: Scans and evicts expired idempotency keys and ephemeral tokens.
   - `system_health_heartbeat()`: Periodic lightweight database and Redis availability probe.
3. **Service & Transport Management Layer (`app/services/schedule_service.py` & `app/routers/schedule_router.py`)**:
   - `ScheduleService`:
     - `get_active_schedules() -> ScheduleListResponse`: Inspects configured beat schedules.
     - `trigger_scheduled_task_manually(task_name: str) -> str`: Allows authorized operations to manually trigger ad-hoc runs in $\mathcal{O}(1)$ lookup time.
   - `schedule_router`:
     - `GET /schedules`: Lists registered periodic tasks and schedules.
     - `POST /schedules/trigger/{task_name}`: Returns HTTP 202 Accepted with correlation `task_id`.
4. **Testing Architecture (`tests/test_celery_beat_schedules.py`)**:
   - Verify schedule registry, crontab evaluation, eager task execution, and HTTP endpoints with 100% pass rate.

---

## 🏗️ Architectural Blueprint: The Single-Leader Metronome Pattern

```
                       ┌─────────────────────────────────────┐
                       │      Celery Beat (Metronome)        │
                       │     Strict Single-Leader (Replicas=1)│
                       │     Reads: celerybeat-schedule      │
                       └──────────────────┬──────────────────┘
                                          │
                   Every Tick (1s)        │  O(1) LPUSH / RPUSH
              Checks crontab/interval     ▼
                       ┌─────────────────────────────────────┐
                       │          Redis Broker               │
                       │       (Queue: 'celery')             │
                       └──────────┬──────────────┬───────────┘
                                  │              │
                    BRPOP (O(1))  │              │  BRPOP (O(1))
                                  ▼              ▼
                       ┌────────────────┐ ┌────────────────┐
                       │ Celery Worker  │ │ Celery Worker  │  (Can scale to 100+ nodes)
                       │   Process 1    │ │   Process 2    │
                       └────────────────┘ └────────────────┘
```

### Architectural Invariants:
1. **Single-Leader Celery Beat Guarantee**:
   - While Celery workers can scale horizontally to hundreds of nodes, **only one Celery Beat process must run at any time**.
   - If two Beat processes run simultaneously, both evaluate the same crontab at midnight and both dispatch tasks to Redis, leading to catastrophic **duplicate financial transactions and double billing**.
2. **Zero-Blocking Metronome Principle**:
   - The Celery Beat process must **never execute task code itself**.
   - Beat only evaluates timestamps and pushes task names + arguments to Redis in $\mathcal{O}(1)$ time ($< 1\text{ms}$). Even if a task takes 4 hours to run, Beat's metronome clock never stalls or drifts.

---

## 📦 Files Created & Modified

### Created
- `app/tasks/scheduled_tasks.py`: Implemented `nightly_reconciliation_audit`, `prune_expired_sessions_and_tokens`, and `system_health_heartbeat`.
- `app/schemas/schedule.py`: Pydantic DTOs `ScheduleEntryResponse`, `ScheduleListResponse`, and `ManualTriggerResponse`.
- `app/services/schedule_service.py`: Business logic for schedule inspection and $\mathcal{O}(1)$ manual task triggering.
- `app/routers/schedule_router.py`: REST endpoints `GET /schedules` and `POST /schedules/trigger/{task_name}`.
- `tests/test_celery_beat_schedules.py`: 11 comprehensive tests validating schedules, crontabs, eager task execution, and APIs.
- `docs/days/day-52.md`: English architectural documentation.
- `docs/days_bn/day-52.md`: 100% Bengali pedagogical guide following the 10-part framework.

### Modified
- `app/core/celery_app.py`: Configured `beat_schedule`, `beat_schedule_filename`, and task includes.
- `app/main.py`: Mounted `schedule_router` at `/schedules`.
- `tests/conftest.py`: Registered `scheduled_tasks` in eager execution fixture.
- `ROADMAP.md`: Marked Day 52 as completed `[x]`.
- `docs/days_bn/README.md`: Appended Day 52 to table of contents.
- `.agents/skills/fastapi-production/SKILL.md`: Codified Patterns #149, #150 and Bad Patterns #120, #121.

---

## 🧪 Verification & Quality Gate Results

### 1. Static Type Analysis (`mypy`)
```bash
mypy --strict app tests alembic
Success: no issues found in 150 source files
```

### 2. Linting & Formatting (`ruff`)
```bash
ruff check app tests alembic
All checks passed!
```

### 3. Dedicated Beat Test Suite (`pytest`)
```bash
pytest tests/test_celery_beat_schedules.py -v
======================== 11 passed, 1 warning in 1.02s ========================
```

### 4. Celery Task Processing & Beat Combined Suite
```bash
pytest tests/test_celery_beat_schedules.py tests/test_celery_task_queue.py -v
======================== 20 passed, 1 warning in 6.14s ========================
```

---

## 💡 Key Architectural Insights

1. **Why `while True: sleep()` is a Disaster in Production**:
   - `sleep(86400)` drifts by several seconds each cycle due to OS scheduling latency and thread wake-up delays.
   - If the container restarts at 23:59, the entire timer resets, causing the midnight job to be skipped entirely.
   - In Celery Beat, schedules are checked against real clock time (`enable_utc=True`) and persisted in `celerybeat-schedule` shelf, ensuring zero drift and resilience across restarts.
2. **Manual Ops Triggering for High-Availability Maintenance**:
   - By exposing `POST /schedules/trigger/{task_name}`, DevOps and site reliability engineers can trigger the nightly audit or garbage collection on-demand before scheduled maintenance, without modifying code or waiting for the cron clock.
