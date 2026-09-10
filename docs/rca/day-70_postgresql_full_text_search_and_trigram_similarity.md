# Root Cause Analysis (RCA): Day 70 - Full-Text Search LIKE Degradation & Typo Intolerance

## 1. Executive Summary

- **Incident Classification**: Search Engine Scalability & Operational Complexity
- **Severity**: High (Full Table Scan CPU Saturation & High Search Bounce Rate)
- **Primary Failure Mode**: Sequential Disk Scans from `LIKE '%term%'` Queries and Zero Typo Tolerance
- **Component Under Analysis**: `app/models/catalog_search.py`, `app/services/search_service.py`, `app/routers/search_router.py`
- **Resolution**: Engineered Native PostgreSQL Full-Text Search Engine with TSVector lexemes, GIN Inverted Indexes, Trigram fuzzy similarity (`pg_trgm`), and an auto-degrading hybrid fallback architecture.

---

## 2. Problem Statement & Symptoms

In product catalog and documentation search workflows:
1. **Unanchored Wildcard CPU Starvation**: Queries structured as `WHERE title ILIKE '%shoes%'` forced full sequential scans across millions of disk blocks ($\mathcal{O}(N)$ disaster), spiking database CPU to 100% under high user search volume.
2. **Linguistic Stemming Blindness**: Substring search failed to match root morphological variations (e.g. searching "run" failed to match "running" or "runs").
3. **Zero Typo Tolerance**: Single-character typos (e.g. "nikke", "adiddas") returned 0 results, directly causing customer abandonment and lost sales conversions.
4. **Elasticsearch Over-Engineering**: Deploying an external Elasticsearch cluster introduced heavy operational overhead, sync drift, CDC pipeline failures, and thousands of dollars in cloud infrastructure spend.

---

## 3. Root Cause Analysis (5 Whys)

1. **Why did product search endpoints take >1,500ms and spike database CPU to 100%?**  
   Because user search requests executed unanchored `ILIKE '%term%'` queries across large catalog tables.
2. **Why can't PostgreSQL B-Tree indexes accelerate `ILIKE '%term%'`?**  
   Because leading wildcards (`%`) prevent B-Tree index traversal, forcing the planner to sequentially scan every single table block.
3. **Why did minor typos like "samsng" return zero products?**  
   Because SQL string equality and `LIKE` require exact substring character alignment without mathematical fuzzy distance metrics.
4. **Why did the team consider deploying Elasticsearch for basic search needs?**  
   Because of unawareness of native PostgreSQL GIN inverted indexing and `pg_trgm` trigram similarity capabilities.
5. **How do we permanently solve this?**  
   By pre-computing weighted `tsvector` columns with GIN inverted indexes for $\mathcal{O}(\log N)$ linguistic search, enabling `pg_trgm` character 3-gram fuzzy similarity ($\ge 0.3$), and deploying an automated hybrid degradation fallback.

---

## 4. Architectural Solution & Implementation

### 4.1 TSVector & Trigram GIN Inverted Indexing (`app/models/catalog_search.py`)
```python
class SearchableProductModel(Base):
    __tablename__ = "searchable_products"
    __table_args__ = (
        # 1. GIN Inverted Index for TSVector lexeme full-text search
        Index("ix_searchable_products_tsv", "search_vector", postgresql_using="gin"),
        # 2. GIN Trigram Indexes for typo-tolerant fuzzy matching
        Index("ix_searchable_products_title_trgm", "title", postgresql_using="gin", postgresql_ops={"title": "gin_trgm_ops"}),
    )
```

### 4.2 Weighted Stemming & Hybrid Fallback Engine (`app/services/search_service.py`)
```python
# 1. Linguistic weighted full-text search
results = await self.search_full_text(query=clean_query, limit=limit)

# 2. Auto-degrade to fuzzy trigram search if 0 exact lexeme matches exist
if not results:
    results = await self.search_fuzzy_trigram(query=clean_query, threshold=0.3, limit=limit)
```

---

## 5. Prevention Rules & Invariants

1. **Zero Leading Wildcard Invariant**: Never deploy user-facing search queries using unanchored `LIKE '%term%'` on production relational tables.
2. **Native GIN First Invariant**: Always leverage PostgreSQL native `TSVector` and `pg_trgm` inverted indexes before introducing external search clusters (Elasticsearch/OpenSearch).
3. **Weighted Relevance Invariant**: Always prioritize title and brand matches with higher weights (Weight 'A' = 1.0) over description text (Weight 'B' = 0.4) using `ts_rank`.
4. **Typo-Tolerant Hybrid Invariant**: Implement an auto-degrading hybrid search pattern so that misspelled queries smoothly fall back to fuzzy trigram matching without empty result screens.
