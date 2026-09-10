# Root Cause Analysis (RCA): Day 69 - Monolithic Table Index Bloat & DELETE Pruning Lock Contention

## 1. Executive Summary

- **Incident Classification**: Database Scalability & Data Lifecycle Architecture
- **Severity**: High (Disk I/O Saturation, Table Locks & Autovacuum Degradation)
- **Primary Failure Mode**: Monolithic 100M+ Row Table Bloat and Destructive `DELETE FROM` Lock Contention
- **Component Under Analysis**: `app/models/audit_log_partitioned.py`, `app/services/partition_service.py`, `app/routers/partition_router.py`
- **Resolution**: Implemented PostgreSQL Declarative Range Partitioning by `created_at`, verified Partition Pruning execution plans, and replaced destructive row-by-row `DELETE` statements with $\mathcal{O}(1)$ partition detachment and drops.

---

## 2. Problem Statement & Symptoms

As high-velocity audit trails and telemetry datasets scaled past 100,000,000 rows:
1. **Destructive DELETE Locks**: Data retention pruning using `DELETE FROM audit_logs WHERE created_at < NOW() - INTERVAL '90 days'` took hours, locked disk pages, and caused severe WAL log inflation.
2. **Autovacuum Starvation & Table Bloat**: Massive bulk row deletions left dead tuples that triggered continuous, heavy autovacuum background workers, saturating server disk I/O and slowing down user-facing writes.
3. **Index Buffer Inefficiency**: Monolithic B-Tree indexes on 100M+ rows exceeded available RAM buffer cache, forcing random disk seeks on every query.

---

## 3. Root Cause Analysis (5 Whys)

1. **Why did database disk I/O hit 100% every night during the retention cleanup job?**  
   Because the cleanup job executed bulk SQL `DELETE` queries across tens of millions of rows.
2. **Why does `DELETE` cause high disk I/O and locks?**  
   Because PostgreSQL MVCC cannot simply delete data; it writes tombstone records to WAL and leaves dead tuples on disk pages until vacuumed.
3. **Why wasn't the table partitioned into date ranges?**  
   Because the table was originally modeled as a single monolithic table without declarative partitioning clauses.
4. **Why did early partitioning attempts fail with schema errors?**  
   Because PostgreSQL requires the partition key (`created_at`) to be part of the composite primary key (`PRIMARY KEY (id, created_at)`), which was violated by a naive scalar primary key.
5. **How do we permanently solve this?**  
   By modeling tables using `PARTITION BY RANGE (created_at)` with composite primary keys, verifying that queries leverage **Partition Pruning** to scan only the relevant child partition, and purging historical data via instant $\mathcal{O}(1)$ `ALTER TABLE DETACH PARTITION` followed by `DROP TABLE`.

---

## 4. Architectural Solution & Implementation

### 4.1 Declarative Partitioning & Composite Primary Key Invariant (`app/models/audit_log_partitioned.py`)
```python
class PartitionedAuditLogModel(Base):
    __tablename__ = "audit_logs"
    __table_args__ = (
        # Mandatory Composite Primary Key Invariant for PostgreSQL Partitioning
        PrimaryKeyConstraint("id", "created_at"),
        {"postgresql_partition_by": "RANGE (created_at)"},
    )

    id: Mapped[uuid.UUID] = mapped_column(default=generate_uuidv7)
    created_at: Mapped[datetime] = mapped_column(default=lambda: datetime.now(UTC))
    ...
```

### 4.2 O(1) Zero-Lock Partition Detach & Drop (`app/services/partition_service.py`)
```python
# O(1) Instant Detachment and Drop without locking the parent table or generating WAL bloat
await session.execute(text(f"ALTER TABLE audit_logs DETACH PARTITION {child_partition_name};"))
await session.execute(text(f"DROP TABLE IF EXISTS {child_partition_name};"))
```

---

## 5. Prevention Rules & Invariants

1. **Composite Primary Key Invariant**: Always include the partition key column (e.g. `created_at`) within the primary key definition of any partitioned table.
2. **Zero-Lock Data Pruning**: Never use bulk `DELETE FROM` statements for time-series or audit log data retention; always detach and drop historical partitions in $\mathcal{O}(1)$ time.
3. **Partition Pruning Verification**: Always verify via `EXPLAIN` that date-range queries filter specifically on partition boundaries to guarantee un-needed child partitions are pruned from execution.
