# Root Cause Analysis (RCA): Day 66 - Sequential Scan CPU Saturation & Inappropriate Index Archetypes

## 1. Executive Summary

- **Incident Classification**: Database Indexing Strategy & Query Plan Performance
- **Severity**: High (Database CPU Saturation & P99 Latency Outage)
- **Primary Failure Mode**: Full Table Sequential Scans ($\mathcal{O}(N)$) on High-Volume Tables and Index-Type Mismatches
- **Component Under Analysis**: `app/models/catalog_item.py`, `app/services/query_plan_service.py`, `app/routers/catalog_router.py`
- **Resolution**: Engineered Multi-Index Specialized Domain Model with 4 distinct indexing archetypes (B-Tree, GIN, BRIN, Hash) and automated `EXPLAIN (ANALYZE, BUFFERS)` execution plan diagnostics.

---

## 2. Problem Statement & Symptoms

Under production database growth (tables exceeding $1,000,000$ rows):
1. **CPU Saturation via Sequential Scans**: Unindexed queries (e.g. searching by `barcode`, filtering by nested JSONB tags, or querying timestamp ranges) force PostgreSQL to perform a `Seq Scan`, scanning every physical disk block into memory. Under concurrent requests, CPU utilization hits 100%, causing query timeouts and cascading backend failure.
2. **Index-Type Mismatch Traps**:
   - Applying standard B-Tree indexes on `JSONB` or array columns fails to accelerate element-containment queries (`@>`), causing the planner to abandon the index and fall back to `Seq Scan`.
   - Applying standard B-Tree indexes on monotonic high-frequency append logs (e.g. audit logs, telemetry, orders) causes massive index bloat, eating gigabytes of RAM in buffer cache and slowing down write transactions.
3. **Lack of Query Plan Visibility**: Developers push queries without verifying planner execution paths, leaving slow `Seq Scan` queries undetected until production traffic spikes.

---

## 3. Root Cause Analysis (5 Whys)

1. **Why did API endpoints searching for catalog items take >2,500ms and crash the database?**  
   Because PostgreSQL executed a `Seq Scan` across 1,000,000 table rows for every incoming request.
2. **Why was a Sequential Scan executed instead of an Index Scan?**  
   Because the columns being filtered (`tags`, `metadata_json`, `barcode`) either had no index or had an incompatible B-Tree index that cannot index individual array elements or JSON keys.
3. **Why did the developer use a standard B-Tree index on a JSONB column?**  
   Because of a misconception that B-Tree indexes work universally for all data types, unaware that B-Tree only indexes the entire JSON text string rather than internal key-value pairs.
4. **Why weren't these sequential scans caught in staging or development?**  
   Because staging databases contain small datasets ($<1,000$ rows) where PostgreSQL cost models favor `Seq Scan` over index lookups due to sequential block read speed, masking the $\mathcal{O}(N)$ bottleneck.
5. **How do we permanently solve this?**  
   By choosing the correct index archetype for the data access pattern (GIN for JSONB/Arrays, BRIN for time-series, Hash for equality, B-Tree for scalars), and deploying an automated query plan diagnostic engine (`QueryPlanService`) that runs `EXPLAIN (ANALYZE, BUFFERS)` and raises warnings whenever `Seq Scan` is detected.

---

## 4. Architectural Solution & Implementation

### 4.1 Specialized Multi-Index Archetypes (`app/models/catalog_item.py`)
```python
__table_args__ = (
    # 1. Composite B-Tree: Multi-column range & sorting
    Index("ix_catalog_category_price", "category", "price"),
    # 2. Hash Index: O(1) equality lookups for barcodes
    Index("ix_catalog_barcode_hash", "barcode", postgresql_using="hash"),
    # 3. BRIN Index: Tiny memory footprint for append-only time series
    Index("ix_catalog_created_at_brin", "created_at", postgresql_using="brin"),
    # 4. GIN Index: Inverted index for JSONB documents and array tags
    Index("ix_catalog_metadata_gin", "metadata_json", postgresql_using="gin"),
    Index("ix_catalog_tags_gin", "tags", postgresql_using="gin"),
)
```

### 4.2 Query Plan Diagnostics & Read-Only Safety (`app/services/query_plan_service.py`)
```python
# Safe read-only guard
QueryPlanService.validate_read_only_query(sql_query)

# Parses planner JSON tree in O(N_nodes) time
report = QueryPlanService.parse_postgres_plan(raw_plan)
if "Seq Scan" in report.scan_types:
    report.warnings.append("Sequential Scan detected! Forces O(N) table scan.")
```

---

## 5. Permanent Prevention Rules

1. **Rule 1 (Index-Pattern Matching)**: Never apply B-Tree indexes blindly to JSONB, arrays, or text search columns. Use **GIN** for containment (`@>`), **BRIN** for append-only time series, **Hash** for exact equality on large strings, and **B-Tree** for scalar range and sort queries.
2. **Rule 2 (Mandatory EXPLAIN ANALYZE Gate)**: Prior to pushing any high-throughput database query to production, verify its execution plan via `EXPLAIN (ANALYZE, BUFFERS)`. Assert that the scan type is `Index Scan`, `Index Only Scan`, or `Bitmap Index Scan`.
3. **Rule 3 (Safe Diagnostic Execution)**: Any diagnostic or query analysis endpoint must strictly reject mutating SQL keywords (`INSERT`, `UPDATE`, `DELETE`, `DROP`, `ALTER`, `TRUNCATE`) to prevent privilege escalation or data corruption.
