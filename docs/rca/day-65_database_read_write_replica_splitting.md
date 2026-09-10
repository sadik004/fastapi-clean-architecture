# Root Cause Analysis (RCA): Day 65 - Primary Database Saturation & Replication Lag Stale Reads

## 1. Executive Summary

- **Incident Classification**: Database Scalability Architecture & Replication Invariants
- **Severity**: High (Database Lockout & Inconsistent Read Prevention)
- **Primary Failure Mode**: Primary Master Database Connection/CPU Exhaustion and Replication Lag Ghost Reads
- **Component Under Analysis**: `app/core/database.py`, `app/core/routing_session.py`, `app/services/product_service.py`
- **Resolution**: Engineered Multi-Engine Database Configuration (Primary + Read Replica) with `RoutingUnitOfWork` dynamic routing, cursor-level mutation shielding, and Read-Your-Own-Writes stickiness.

---

## 2. Problem Statement & Symptoms

Under high traffic (95% read, 5% write):
1. **Primary Master Saturation (Connection Exhaustion)**: Every catalog browse and search query acquires a connection from the master database pool. During high-traffic events, the pool is fully exhausted, causing mutating operations (checkout, payments, profile updates) to time out with `PoolTimeoutError`.
2. **Replication Lag Stale Read Inconsistency**: When read queries are naively redirected to a read replica, asynchronous replication lag (e.g. 50ms to 500ms) causes users who just updated their profile or placed an order to see outdated data upon immediate page reload, triggering user confusion and duplicate write attempts.
3. **Accidental Replica Corruption Risk**: Without low-level driver constraints, developer error or wrong routing configurations might execute an `UPDATE` or `INSERT` on the read replica, corrupting replica state or triggering split-brain errors.

---

## 3. Root Cause Analysis (5 Whys)

1. **Why did customer checkouts fail with connection timeouts during peak sales?**  
   Because all database connections in the master pool were occupied by search and catalog browsing queries.
2. **Why were catalog browsing queries hitting the master database?**  
   Because the system lacked a separate Read Replica engine to absorb read-heavy traffic.
3. **Why can't we simply route all SELECT queries to a Read Replica?**  
   Because immediately after a user writes data, the replica has not yet replicated the write-ahead log (WAL) from the master.
4. **What happens if a read query hits the replica during replication lag?**  
   The user reads stale, outdated data (violating the Read-Your-Own-Writes contract).
5. **How do we permanently solve both issues?**  
   By establishing two independent connection pools, dynamically routing reads to the replica, and maintaining a sticky flag (`_has_written = True`) in `RoutingUnitOfWork` that forces all subsequent reads within that transaction context to stick to the Primary master engine.

---

## 4. Architectural Solution & Implementation

### 4.1 Multi-Engine Configuration (`app/core/database.py`)
```python
primary_engine = create_async_engine(settings.database_url, pool_size=20, max_overflow=10)
replica_engine = create_async_engine(settings.database_read_replica_url or settings.database_url, pool_size=30, max_overflow=20)

PrimaryAsyncSession = async_sessionmaker(bind=primary_engine, expire_on_commit=False)
ReplicaAsyncSession = async_sessionmaker(bind=replica_engine, expire_on_commit=False)
```

### 4.2 Read-Your-Own-Writes Lag Guard (`app/core/routing_session.py`)
```python
@property
def active_read_session(self) -> AsyncSession:
    if self._has_written:
        return self.primary_session
    return self.replica_session
```

### 4.3 Cursor-Level Mutation Shield
```python
@event.listens_for(replica_engine.sync_engine, "before_cursor_execute")
def _guard_replica_mutations(conn, cursor, statement, parameters, context, executemany):
    normalized = statement.strip().upper()
    for forbidden in ("INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "CREATE", "TRUNCATE"):
        if normalized.startswith(forbidden):
            raise ReadOnlyReplicaMutationException(
                f"Mutating operation '{forbidden}' is strictly prohibited on read replica engine."
            )
```

---

## 5. Preventative Rules & Guardrails

1. **Master Protection Rule**: All high-volume read-only endpoints must acquire sessions from `ReplicaAsyncSession` or `get_read_session`.
2. **Lag Guard Mandate**: Any service executing a state mutation followed by a read within the same request lifecycle must use `RoutingUnitOfWork` to preserve Read-Your-Own-Writes consistency.
3. **Driver Shielding**: Mutation attempts on read replica engines must fail-fast at the driver layer rather than relying on application code conventions.
