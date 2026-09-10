# Root Cause Analysis (RCA): Day 68 - Database Connection Pool Starvation & Idle-in-Transaction Leaks

## 1. Executive Summary

- **Incident Classification**: Database Reliability & Connection Lifecycle
- **Severity**: Critical (Global Application Outage & Connection Pool Starvation)
- **Primary Failure Mode**: Unbounded Hanging Database Statements and Leaked Idle-in-Transaction Sockets
- **Component Under Analysis**: `app/core/database.py`, `app/core/config.py`, `app/core/exception_handlers.py`
- **Resolution**: Enforced 3-tier server-side PostgreSQL timeouts (`statement_timeout=3s`, `idle_in_transaction_session_timeout=5s`, `lock_timeout=2s`), pool health pre-ping validation (`pool_pre_ping=True`), and mandatory `session.rollback()` anti-poisoning guards.

---

## 2. Problem Statement & Symptoms

Under production load:
1. **Connection Pool Exhaustion**: A single runaway query or lock contention held database sockets indefinitely. New HTTP requests trying to check out a connection blocked until raising `TimeoutError: QueuePool limit of size 20 overflow 10 reached`.
2. **Idle-in-Transaction Poisoning**: Endpoints performing external HTTP requests inside an open transaction held database locks open. Abrupt client disconnects left the transaction open on the PostgreSQL server, blocking autovacuum and accumulating table deadlocks.
3. **Stale Broken Connections**: Firewall drops and TCP socket timeouts caused pooled connections to become stale, resulting in `psycopg2.OperationalError: server closed the connection unexpectedly` on random customer requests.

---

## 3. Root Cause Analysis (5 Whys)

1. **Why did all FastAPI workers stop responding with HTTP 500/504 errors?**  
   Because the database connection pool ran out of available connection slots.
2. **Why were all 30 pool connections checked out and unavailable?**  
   Because several slow queries took >60 seconds waiting on unindexed locks, while other connections were held idle inside unfinished transactions.
3. **Why didn't the database cancel these queries automatically?**  
   Because PostgreSQL default server settings allow queries and idle transactions to run indefinitely (`statement_timeout=0`).
4. **Why weren't dead connections cleaned up before checkout?**  
   Because `pool_pre_ping` was disabled, allowing stale severed sockets to be dispatched to incoming requests.
5. **How do we permanently solve this?**  
   By configuring server-side PostgreSQL timeout arguments in `connect_args["server_settings"]`, enabling `pool_pre_ping=True`, and establishing global exception handlers that guarantee `await session.rollback()` on query timeouts.

---

## 4. Architectural Solution & Implementation

### 4.1 Server-Side PostgreSQL Timeout Hardening (`app/core/database.py`)
```python
connect_args = {
    "server_settings": {
        "statement_timeout": str(settings.db_statement_timeout_ms),  # 3000ms
        "idle_in_transaction_session_timeout": str(settings.db_idle_in_transaction_timeout_ms),  # 5000ms
        "lock_timeout": str(settings.db_lock_timeout_ms),  # 2000ms
    }
}
```

### 4.2 Pool Pre-Ping & Anti-Leak Lifecycle (`app/core/database.py`)
```python
engine = create_async_engine(
    settings.database_url,
    pool_size=settings.db_pool_size,
    max_overflow=settings.db_max_overflow,
    pool_timeout=settings.db_pool_timeout,
    pool_recycle=settings.db_pool_recycle,
    pool_pre_ping=settings.db_pool_pre_ping,
    connect_args=connect_args,
)
```

---

## 5. Prevention Rules & Invariants

1. **Mandatory Server-Side Timeouts**: Never establish a production PostgreSQL connection pool without explicit `statement_timeout`, `idle_in_transaction_session_timeout`, and `lock_timeout` settings.
2. **Pessimistic Pre-Ping Invariant**: Always enable `pool_pre_ping=True` on long-lived connection pools to proactively eliminate severed or stale TCP sockets.
3. **Mandatory Rollback on Exception**: Always execute `await session.rollback()` in `finally:` or `except:` blocks before releasing an asynchronous session back to the pool to prevent poisoned session state.
4. **Fast-Fail Degradation**: Map query cancellations to HTTP 504 Gateway Timeout and pool exhaustion to HTTP 503 Service Unavailable with standardized `Retry-After: 5` headers.
