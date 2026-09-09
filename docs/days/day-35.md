# Day 35: Bloom Filter Architecture to Prevent Cache Penetration (Probabilistic Membership Testing)

**Date**: 2026-09-09  
**Role**: Junior Apprentice Backend Engineer  
**Lead Architect & Mentor**: User  
**Milestone**: Month 2 — Distributed Systems, Caching & Security  

---

## 1. Concepts Covered Today

- **The Cache Penetration Vulnerability**:
  - When clients query for entities that do not exist (e.g. automated security scanners, brute-force crawlers, or distributed denial-of-service attacks sending millions of random UUIDs), every single request results in a **Cache Miss** in Redis.
  - Because the cache holds no entry, the request falls directly through to PostgreSQL.
  - The database executes expensive index scans, finds nothing, and wastes heavy disk I/O, saturating worker threads and exhausting database connection pools.
  - Traditional mitigations (such as caching empty/null values) suffer from cache pollution and memory exhaustion when attackers generate unbounded random keys.

- **Bloom Filter Probabilistic Data Structure**:
  - A space-efficient probabilistic data structure invented by Burton Howard Bloom in 1970 for set membership testing.
  - **Fundamental Guarantees**:
    - **Zero False Negatives**: If the filter returns `False`, the element is **definitely NOT** in the set.
    - **Bounded False Positives**: If the filter returns `True`, the element **probably IS** in the set, with a mathematically bounded false positive probability $P$.
  - **Trade-off**: Deletions are not supported in standard Bloom filters because resetting a bit to 0 might corrupt membership for other keys that share that hashed bit index.

- **Optimal Sizing Mathematics**:
  - Given expected capacity $N$ and target false positive rate $P \in (0, 1)$:
    - Optimal bit array size $m$:
      $$m = -\frac{N \ln(P)}{(\ln 2)^2} \approx -\frac{N \ln(P)}{0.480453}$$
    - Optimal number of hash functions $k$:
      $$k = \frac{m}{N} \ln 2 \approx \frac{m}{N} \times 0.693147$$
  - **Memory Efficiency**:
    - For $N = 100,000$ and $P = 0.01$ (1% FPR):
      $$m = 958,506 \text{ bits} \implies 119,814 \text{ bytes} \approx 119.8 \text{ KB}$$
    - Even for $1,000,000$ users, $m \approx 9.58 \text{ million bits} \approx 1.14 \text{ MB}$, well within strict sub-1.5 MB RAM budgets.

- **Kirsch-Mitzenmacher Double Hashing**:
  - Evaluating $k$ independent hash functions with SHA-256 or MD5 is computationally prohibitive.
  - The Kirsch-Mitzenmacher optimization (Harvard/ESA 2006) proves that only **two independent hash functions** $h_1(x)$ and $h_2(x)$ are needed to simulate $k$ independent hashes without degrading the false positive rate:
    $$g_i(x) = (h_1(x) + i \cdot h_2(x)) \pmod m \quad \text{for } i \in [0, k-1]$$
  - We derive $h_1$ and $h_2$ from two distinct 64-bit slices of `hashlib.sha256(item.encode("utf-8")).digest()`, guaranteeing deterministic, uniform bit distribution independent of Python process restart salt.

- **Step 0 Bloom Filter Shield in Domain Services**:
  - In `UserService.get_user_by_id` and `UserService.get_user_by_id_xfetch`:
    - **Step 0 (Bloom Filter Guard)**: Before querying Redis or PostgreSQL, verify `user_id in self._bloom`.
    - If `False`: Immediately raise `UserNotFoundException(user_id)`.
    - Result: **0 Redis lookups and 0 PostgreSQL queries** for all non-existent IDs, completely deflecting penetration traffic in $\mathcal{O}(k)$ sub-microsecond time.
    - If `True`: Proceed through normal Cache-Aside / XFetch workflow.

- **Application Lifecycle Startup Seeding**:
  - Integrated into FastAPI's `lifespan` startup hook in `app/main.py`.
  - Queries `repo.get_all_ids()` (`SELECT id FROM users`) and populates the in-memory singleton `BloomFilter`.
  - When new users register via `UserService.register_user`, their UUID is immediately added to the filter (`self._bloom.add(user.id)`).

- **Operational Telemetry**:
  - `GET /metrics/bloom-filter`: Real-time operational metrics including capacity, bit count, hash count, estimated items, fill ratio, and memory consumption.
  - `GET /metrics/bloom-filter/check/{user_id}`: Diagnostics probe to check key membership without database queries.

---

## 2. Key Code Artifacts

- `app/core/dsa/bloom_filter.py`:
  - Slotted class `BloomFilter` using `bytearray` bit storage and Kirsch-Mitzenmacher double hashing.
  - Implements `add(item)`, `contains(item)`, `__contains__(item)`, `clear()`, and `get_metrics()`.
  - Includes input validation ($N > 0$, $0 < P < 1$) and mathematical estimators.
- `app/core/dsa/__init__.py`:
  - Exported `BloomFilter`.
- `app/repositories/user_repository.py`:
  - Added `get_all_ids()` to `UserRepositoryProtocol` and `InMemoryUserRepository`.
- `app/repositories/sqlalchemy_user_repository.py`:
  - Implemented `get_all_ids()` using `select(UserModel.id)`.
- `app/services/user_service.py`:
  - Introduced module-level `BloomFilter` singleton, `get_user_bloom_filter()`, and `seed_user_bloom_filter()`.
  - Injected `bloom_filter` into `UserService`.
  - Added Step 0 guard in `get_user_by_id` and `get_user_by_id_xfetch`.
  - Auto-seeded new user IDs in `register_user`.
- `app/core/dependencies.py`:
  - Injected `bloom_filter` into `get_user_service` dependency with test-isolation guard for `AsyncMock`.
  - Re-exported `get_user_bloom_filter`.
- `app/schemas/metrics.py`:
  - Defined `BloomFilterMetricsResponse` and `BloomFilterCheckResponse`.
- `app/routers/metrics_router.py`:
  - Exposed `GET /metrics/bloom-filter` and `GET /metrics/bloom-filter/check/{user_id}`.
- `app/main.py`:
  - Added lifespan startup hook executing `seed_user_bloom_filter(repo)`.
- `tests/conftest.py`:
  - Added `get_user_bloom_filter().clear()` in `clean_repo` fixture to ensure test isolation.
- `tests/test_bloom_filter.py`:
  - 9 thorough test suites validating zero false negatives, bounded false positives ($\le 1.5\%$), sub-1.5 MB memory footprint, zero DB/Cache query deflection, registration seeding, and HTTP telemetry endpoints.

---

## 3. Verification & Quality Gates

- **Test Suite**: 390 passing tests (`pytest tests/ -v` passed in ~32s).
- **Static Typing**: `mypy --strict app tests alembic` passed with 0 errors across 85 files.
- **Linting & Formatting**: `ruff check .` and `ruff format --check .` passed with 0 errors.
