# Root Cause Analysis (RCA): Day 67 - Keyset Cursor Pagination & Offset Degradation

## 1. Executive Summary

- **Incident Classification**: Database Performance & High-Volume Data Access
- **Severity**: High (Database Memory & I/O Saturation on Deep Pagination)
- **Primary Failure Mode**: $\mathcal{O}(N)$ Full Sequential Offset Scanning and Page Skips/Duplicate Anomalies
- **Component Under Analysis**: `app/core/pagination/cursor.py`, `app/repositories/order_repository.py`, `app/routers/order_router.py`
- **Resolution**: Implemented Keyset / Cursor-Based Pagination with Opaque Base64 Cursors and Composite Unique Tie-Breaker Ordering.

---

## 2. Problem Statement & Symptoms

In high-concurrency production environments serving paginated lists (e.g. order feeds, audit trails):
1. **$\mathcal{O}(N)$ Offset Degradation**: When clients navigate to deep pages using traditional SQL `OFFSET 100000 LIMIT 20`, PostgreSQL must physically fetch and scan all preceding 100,000 rows from disk/buffer before discarding them and returning the slice, causing catastrophic CPU spikes and database timeouts.
2. **Concurrent Mutation Ghost Items & Page Skips**: When new rows are inserted or deleted while a client paginates using `OFFSET`, rows shift dynamically across offset boundaries, resulting in duplicate rows or missed items.
3. **Database Column Leakage**: Passing raw column parameters (e.g. `?order_by=created_at&after_id=500`) in URLs leaks internal database schemas.
4. **Redundant `COUNT(*)` Latency**: Running expensive `SELECT COUNT(*)` on multi-million row tables on every pagination request doubles database load.

---

## 3. Root Cause Analysis (5 Whys)

1. **Why did paginated order endpoints time out during mobile infinite scroll?**  
   Because the database took >3,000ms to resolve queries on deep page offsets (`OFFSET 250000`).
2. **Why does `OFFSET` take seconds on large tables?**  
   Because SQL `OFFSET` is not an index seek; the query planner must sequentially scan and discard $N$ rows.
3. **Why did users encounter duplicate orders while scrolling?**  
   Because new orders were inserted at the top of the table concurrently, shifting the physical row positions relative to the offset integer.
4. **Why did the application expose internal database column names in URL query parameters?**  
   Because pagination state was passed as raw un-encoded query parameters instead of opaque, serialized tokens.
5. **How do we permanently solve this?**  
   By implementing Keyset / Cursor Pagination anchoring queries on `WHERE (created_at, id) < (:last_created_at, :last_id)`, encoding state into opaque URL-safe Base64 tokens (`CursorCodec`), and using the $Limit + 1$ probe technique to eliminate `COUNT(*)` queries.

---

## 4. Architectural Solution & Implementation

### 4.1 URL-Safe Opaque Base64 Cursor Tokenization (`app/core/pagination/cursor.py`)
```python
class CursorCodec:
    @staticmethod
    def encode(created_at: datetime, item_id: uuid.UUID) -> str:
        payload = {"c": created_at.isoformat(), "id": str(item_id)}
        raw_json = json.dumps(payload, separators=(",", ":"))
        return base64.urlsafe_b64encode(raw_json.encode()).decode().rstrip("=")

    @staticmethod
    def decode(cursor_str: str) -> tuple[datetime, uuid.UUID]:
        # Validates base64 structure and enforces cryptographic safety
        ...
```

### 4.2 Constant-Time B-Tree Index Seeking (`app/repositories/order_repository.py`)
```python
# O(1) B-Tree Seek eliminating linear table scanning
query = (
    select(OrderModel)
    .where(
        tuple_(OrderModel.created_at, OrderModel.id) < (cursor_created_at, cursor_id)
    )
    .order_by(OrderModel.created_at.desc(), OrderModel.id.desc())
    .limit(limit + 1)  # Probe technique
)
```

---

## 5. Prevention Rules & Invariants

1. **Zero SQL Offset Invariant**: Never use `LIMIT ... OFFSET ...` on production tables exceeding 10,000 rows for continuous feeds or infinite scrolling.
2. **Unique Tie-Breaker Invariant**: Always include a unique, non-nullable primary key tie-breaker (`id DESC`) in keyset pagination ordering to eliminate row duplication across page boundaries.
3. **Opaque Token Invariant**: Never expose raw database column names or raw entity IDs in URL pagination parameters. Always serialize and validate composite cursors through `CursorCodec`.
4. **$Limit + 1$ Probe Invariant**: Never execute `COUNT(*)` on paginated endpoints; evaluate `has_more` using the $\text{limit} + 1$ row fetch strategy.
