# Day 16: ডেটাবেস কানেকশন পুলিং আর্কিটেকচার — `pool_size`, `max_overflow` ও `pool_pre_ping` দিয়ে শূন্য-লিক সংযোগ ব্যবস্থাপনা

---

## ১. বাস্তব জীবনের গল্প ও উপমা (Real-World Analogy)

কল্পনা করুন ঢাকার একটি ব্যস্ত হাসপাতালের জরুরি বিভাগ। হাসপাতালে মাত্র ২০টি স্ট্রেচার আছে। প্রতিদিন স্বাভাবিক সময়ে ২০ জন রোগী আসে — প্রতিটি রোগী একটি স্ট্রেচার নেয়, ব্যবহার করে, ফিরিয়ে দেয়। কোনো স্ট্রেচার কেনা লাগে না, অপেক্ষার প্রয়োজন নেই — এটাই **`pool_size = 20`**।

কিন্তু ঈদের দিনে হঠাৎ ৩০ জন রোগী একসাথে আসে। ২০টি স্ট্রেচার শেষ। হাসপাতাল কর্তৃপক্ষ ব্যাক-স্টোর থেকে আরো ১০টি ইমার্জেন্সি স্ট্রেচার বের করে — এটাই **`max_overflow = 10`**। মোট ৩০ জন রোগী সামলানো গেলো। কিন্তু স্বাভাবিক সময়ে ফিরলে এই অতিরিক্ত ১০টি স্ট্রেচার ব্যাক-স্টোরে চলে যায় — কারণ সেগুলো "warm" রাখার কোনো মানে নেই।

এখন ধরুন কোনো স্ট্রেচার রাতে অব্যবহৃত পড়ে থাকে এবং তার চাকার নাটবোল্ট ঢিলা হয়ে যায় — একজন রোগীকে তুললে সে ভেঙে পড়বে। তাই হাসপাতাল প্রতিটি স্ট্রেচার ব্যবহারের আগে একটি দ্রুত চেক করে: **এখনো কাজের?** — এটাই **`pool_pre_ping = True`** (SELECT 1 health-check)।

আমাদের FastAPI অ্যাপ হলো সেই হাসপাতাল, আর ডেটাবেস কানেকশন হলো সেই স্ট্রেচার।

---

## ২. এটা না বানালে কী মহাবিপদ হতো? (The Production Disaster Without It)

**ছাড়া কী হতো:**

**দৃশ্যকল্প ১ — Connection Exhaustion (কানেকশন শেষ):**  
প্রতিটি রিকোয়েস্টে নতুন TCP কানেকশন খুলতে থাকলে প্রতিটি কানেকশনে খরচ হয়: TCP handshake (~50ms) + Database authentication (~20ms) + SSL negotiation (~30ms) = **১০০ms+ overhead per request**। ১০,০০০ concurrent users-এ এটা ডেটাবেস সার্ভারের file descriptor limit ছাড়িয়ে `Too many open connections` error দিয়ে পুরো সার্ভার ক্র্যাশ করে।

**দৃশ্যকল্প ২ — Stale Connection (মরা সংযোগ):**  
AWS RDS বা Azure Database সাধারণত ৮ ঘণ্টা নিষ্ক্রিয় থাকা কানেকশন নিজে বন্ধ করে দেয়। কিন্তু Python-এর connection pool সেটা জানে না — সে ঐ মৃত কানেকশনটাই পরবর্তী রিকোয়েস্টে দেয়। ফলে:
```
sqlalchemy.exc.OperationalError: (psycopg2.OperationalError) server closed the connection unexpectedly
```
...এবং ব্যবহারকারী HTTP 500 পায়।

**দৃশ্যকল্প ৩ — Connection Leak (কানেকশন লিক):**  
`try...finally` ব্লকের বাইরে exception হলে কানেকশন pool-এ ফেরত যায় না। ধীরে ধীরে সব কানেকশন "checked out" হয়ে থাকে, নতুন রিকোয়েস্ট `QueuePool limit of size 20 overflow 10 reached` error পায়।

---

## ৩. আর্কিটেকচারাল ডিজাইন ও ইঞ্জিন কীভাবে কাজ করে (System Design)

### Connection Pool এর অভ্যন্তরীণ কাঠামো

```
                      [FastAPI Application]
                             |
              ┌──────────────▼──────────────┐
              │        QueuePool             │
              │  ┌───────┐  ┌───────┐  ...  │  ← pool_size = 20 (warm connections)
              │  │ conn1 │  │ conn2 │  ...  │
              │  └───────┘  └───────┘       │
              │  ┌───────┐  ← max_overflow  │  ← Burst buffer (10 extra)
              │  │ connX │    connections    │
              │  └───────┘                   │
              └────────────┬────────────────┘
                           │
                           ▼
                   [PostgreSQL / SQLite]
```

**তিনটি অপারেশনের সময় জটিলতা:**
| অপারেশন | Big-O | কারণ |
|---|---|---|
| Connection Checkout | **O(1)** | Internal deque থেকে pop |
| Connection Checkin | **O(1)** | Internal deque-তে push |
| Pre-ping health check | **O(1)** | একটি `SELECT 1` query |
| Pool telemetry query | **O(1)** | In-memory counter read |

### Sizing Formula
```
pool_size = Worker Processes × Average Active Queries Per Worker

max_overflow ≈ 0.5 × pool_size
```
আমাদের প্রজেক্টে: `pool_size=20`, `max_overflow=10` → সর্বোচ্চ **৩০টি concurrent database connections**।

---

## ৪. আমাদের প্রজেক্টের আসল কোড ও লাইনের সহজ ব্যাখ্যা (Real Code Breakdown)

### `app/core/config.py` — Configuration Parameters

```python
# Settings class-এ যোগ করা নতুন fields:
db_pool_size: int = 20        # ২০টি warm connection সবসময় ready
db_max_overflow: int = 10     # burst-এ আরো ১০টি temporary connection
db_pool_timeout: float = 30.0 # ৩০ সেকেন্ড অপেক্ষার পর TimeoutError
db_pool_recycle: int = 1800   # ৩০ মিনিটের পুরনো connection ফেলে দাও
db_pool_pre_ping: bool = True # প্রতিটি checkout-এর আগে SELECT 1 চেক
```

**`db_pool_recycle = 1800` কেন?**  
AWS RDS, Azure Database, বা load balancer সাধারণত ৩০–৬০ মিনিটের idle connection kill করে। `pool_recycle=1800` নিশ্চিত করে SQLAlchemy নিজেই সেই কানেকশন ফেলে দেয় — Cloud firewall-এর "sudden death" আর কাউকে HTTP 500 দেয় না।

---

### `app/core/database.py` — Engine Configuration

```python
# ১. Universal pool kwargs — সব database dialect-এ কাজ করে
pool_kwargs: dict[str, Any] = {
    "pool_pre_ping": settings.db_pool_pre_ping,  # SELECT 1 health-check
    "pool_recycle": settings.db_pool_recycle,    # ৩০ মিনিটে recycle
}

# ২. QueuePool-only kwargs — :memory: SQLite-এ চলে না
is_sqlite_memory = ":memory:" in settings.database_url
if not is_sqlite_memory:
    pool_kwargs["pool_size"] = settings.db_pool_size      # baseline: 20
    pool_kwargs["max_overflow"] = settings.db_max_overflow # burst: 10
    pool_kwargs["pool_timeout"] = settings.db_pool_timeout # timeout: 30s

# ৩. Engine তৈরি করা হয় একটি singleton হিসেবে
engine: AsyncEngine = create_async_engine(
    settings.database_url,
    echo=settings.db_echo,
    connect_args=connect_args,
    **pool_kwargs,
)
```

**`:memory:` SQLite কেন আলাদা?**  
In-memory SQLite `StaticPool` ব্যবহার করে — শুধুমাত্র একটিই connection থাকে। `pool_size`, `max_overflow`, `pool_timeout` দিলে `TypeError` পাওয়া যায়। তাই `is_sqlite_memory` check দিয়ে সেই parameters skip করা হয়।

---

### `app/core/database.py` — Pool Telemetry

```python
def get_db_pool_status() -> dict[str, Any]:
    pool = engine.pool
    checked_in  = pool.checkedin()  # idle connections
    checked_out = pool.checkedout() # active connections
    overflow    = max(0, pool.overflow())  # beyond pool_size
    pool_size   = pool.size()

    return {
        "pool_type": "QueuePool",
        "pool_size": pool_size,
        "checked_in_connections": checked_in,
        "checked_out_connections": checked_out,
        "overflow_connections": overflow,
        "total_open_connections": checked_in + checked_out,
    }
```

এই function-টি `GET /health/db/pool` endpoint-এ expose করা হয়েছে। প্রতিটি metric read করা **O(1)** — pool-এর internal counter থেকে সরাসরি পড়া।

---

### `app/core/database.py` — Zero-Leak Session Generator

```python
async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    session: AsyncSession = async_session_factory()
    try:
        yield session           # ← রিকোয়েস্ট handle হয়
        await session.commit()  # ← সবকিছু ঠিক থাকলে commit
    except Exception:
        await session.rollback() # ← যেকোনো error-এ rollback
        raise
    finally:
        await session.close()  # ← সবসময় connection pool-এ ফেরত!
```

**`finally` ব্লক কেন জীবনরক্ষাকারী?**  
Python-এ `try...except`-এর পরে exception re-raise হলেও `finally` block সবসময় চলে। এটা নিশ্চিত করে যে `session.close()` — এবং তার মাধ্যমে pool-এ connection return — কখনো skip হয় না। এটাই **Zero Connection Leak Contract**।

---

## ৫. DSA সহজ ভাষায় বিশ্লেষণ (DSA Complexity Made Simple)

### Connection Pool কে বোঝা: Bounded Deque as a Resource Manager

Python-এর SQLAlchemy QueuePool অভ্যন্তরীণভাবে একটি **thread-safe deque** রক্ষণাবেক্ষণ করে:

```
Pool (Deque):   [conn1] [conn2] [conn3] ... [conn20]
                  ↑                              ↑
              checkout (pop)              checkin (append)
```

- **Checkout**: `deque.popleft()` — O(1)
- **Checkin**: `deque.append()` — O(1)
- **Space**: Fixed O(pool_size + max_overflow) = O(30) — constant memory

এই bounded deque-ই নিশ্চিত করে যে memory usage কখনো unbounded হয় না — তা যতই concurrent request আসুক না কেন।

### Sizing গণিত

```
Total Capacity = pool_size + max_overflow = 20 + 10 = 30 connections

Max Wait Time = pool_timeout = 30 seconds

Connection Lifetime = pool_recycle = 1800 seconds
```

যদি ৩১তম request আসে এবং সব ৩০টি connection busy থাকে:
- SQLAlchemy `pool_timeout` (30s) পর্যন্ত অপেক্ষা করে
- তারপর `sqlalchemy.exc.TimeoutError` raise করে → FastAPI HTTP 503 দেয়

এটা **fail-fast** আচরণ — অনির্দিষ্টকাল hang করার বদলে দ্রুত ব্যর্থ হওয়া।

---

## ৬. এই দিনের RCA ও বাস্তব ভুল থেকে শিক্ষা (Real RCA Incident)

**এই দিনের নির্দিষ্ট কোনো RCA নথি না থাকলেও**, connection pooling বিষয়ে সবচেয়ে সাধারণ production failure হলো **Stale Connection under Cloud Firewall**:

**লক্ষণ:** রাত ৩টায় traffic কম থাকলে, সকাল ৯টায় প্রথম request-এ:
```
OperationalError: (psycopg2.OperationalError) server closed the connection unexpectedly
```

**কারণ:** রাতে idle কানেকশন AWS NAT Gateway বা RDS Proxy-তে ৬০০ সেকেন্ড পরে kill হয়। Pool জানে না।

**সমাধান:**
```python
# pool_pre_ping=True → প্রতিটি checkout-এ "SELECT 1" চালানো হয়
# যদি connection মৃত হয় → transparently নতুন connection তৈরি হয়
# User কোনো error দেখে না!

pool_kwargs["pool_pre_ping"] = True  # ← এই এক লাইনই যথেষ্ট
```

**`pool_recycle=1800` সাথে `pool_pre_ping=True`** — এই দুটি মিলে একটি "belt and suspenders" প্রতিরক্ষা তৈরি করে:
- `pool_recycle`: ৩০ মিনিটের পুরনো connection আগেই ফেলে দাও
- `pool_pre_ping`: যদি কোনোভাবে পুরনো connection থাকে, ব্যবহারের আগে test করো

---

## ৭. টেস্টিং স্ট্র্যাটেজি ও কোয়ালিটি গেট (Testing Strategy)

`tests/test_database_connection_pooling.py` — ৫টি সুনির্দিষ্ট test:

### Test 1: Configuration Validation
```python
def test_engine_pool_configuration():
    """pool_recycle, pool_pre_ping, এবং pool_size settings সঠিকভাবে engine-এ লোড হয়েছে কিনা।"""
    assert engine.pool._recycle == settings.db_pool_recycle
    assert engine.pool._pre_ping == settings.db_pool_pre_ping
```

### Test 2: Pre-ping Recovery
```python
async def test_pool_pre_ping_transparent_stale_connection_recovery():
    """Connection invalidate করলেও পরবর্তী session কোনো error ছাড়াই কাজ করে।"""
    async with async_session_factory() as session:
        # Connection invalidate করা হলো
        await session.connection()
        session.get_bind().invalidate()

    # পরবর্তী session নতুন connection নিয়ে কাজ করে
    async with async_session_factory() as session:
        result = await session.execute(text("SELECT 1"))
        assert result.scalar() == 1
```

### Test 3: Zero Leak Contract
```python
async def test_concurrent_session_checkout_zero_leak_contract():
    """২৫টি concurrent session শেষ হওয়ার পর checked_out_connections = 0।"""
    async def use_session():
        async with async_session_factory() as s:
            await s.execute(text("SELECT 1"))

    await asyncio.gather(*[use_session() for _ in range(25)])

    status = get_db_pool_status()
    assert status["checked_out_connections"] == 0  # শূন্য লিক!
```

### Test 4: Health Endpoint
```python
async def test_health_db_pool_endpoint_success(async_client):
    """GET /health/db/pool সঠিক JSON structure ও integrity formula দেয়।"""
    response = await async_client.get("/health/db/pool")
    data = response.json()
    # Integrity check: total = checked_in + checked_out
    assert data["total_open_connections"] == (
        data["checked_in_connections"] + data["checked_out_connections"]
    )
```

### Test 5: Exhaustion Guard
```python
async def test_pool_exhaustion_timeout_guard():
    """pool_size + max_overflow ছাড়িয়ে গেলে TimeoutError পাওয়া যায়।"""
    # ... ৩০টির বেশি connection ধরে রেখে ৩১তম চেষ্টা করলে TimeoutError
```

**Quality Gate:** 261 passed, 100% pass rate।

---

## ৮. ইন্টারভিউ ও ভাইভা প্রশ্ন (Interview Questions & Model Answers)

**প্রশ্ন ১:** "Connection Pool কী এবং কেন দরকার?"

**মডেল উত্তর:** প্রতিটি database connection তৈরি করতে TCP handshake + authentication মিলিয়ে ১০০ms+ সময় লাগে। Connection Pool হলো pre-warmed connections-এর একটি bounded cache — checkout O(1), checkin O(1)। বড় অ্যাপে এটি ছাড়া প্রতিটি request-এ ১০০ms+ overhead মানে ১০,০০০ req/s-এ ১ সেকেন্ডের বদলে ১০ মিনিট লাগার মতো পরিস্থিতি।

---

**প্রশ্ন ২:** "`pool_pre_ping=True` এর পার্শ্বপ্রতিক্রিয়া কী?"

**মডেল উত্তর:** প্রতিটি connection checkout-এ একটি `SELECT 1` পাঠানো হয় — এটি সামান্য latency overhead (sub-millisecond) যোগ করে। তবে এই ট্রেডঅফ সম্পূর্ণ ন্যায্য কারণ বিকল্প হলো production-এ `OperationalError: server closed the connection` এবং HTTP 500। Cloud-hosted databases-এ এটি mandatory।

---

**প্রশ্ন ৩:** "`pool_size=20, max_overflow=10` থাকলে ৩১তম concurrent request কী হবে?"

**মডেল উত্তর:** SQLAlchemy `pool_timeout=30.0` সেকেন্ড পর্যন্ত queue-তে অপেক্ষা করে। কোনো connection মুক্ত না হলে `sqlalchemy.exc.TimeoutError` raise করে। FastAPI এটি ধরে HTTP 503 Service Unavailable দেয়। এটি fail-fast design — অনির্দিষ্টকাল hang করার চেয়ে ভালো।

---

**প্রশ্ন ৪:** "In-memory SQLite-এ `pool_size` কেন দেওয়া যায় না?"

**মডেল উত্তর:** In-memory SQLite `StaticPool` ব্যবহার করে — মাত্র একটি connection, কারণ `:memory:` database process-specific। `pool_size`, `max_overflow` দিলে `TypeError: Invalid argument` পাওয়া যায়। তাই আমরা `is_sqlite_memory` flag check করে QueuePool parameters conditionally apply করি।

---

## ৯. এক নজরে আসল মূল লজিক (Junior Architect's Operational Checklist)

```
✅ pool_size  = worker_count × avg_concurrent_queries_per_worker
✅ max_overflow ≈ 50% of pool_size (burst buffer)
✅ pool_timeout = 30s (fail-fast, not hang-forever)
✅ pool_recycle = 1800s (shorter than cloud firewall idle timeout)
✅ pool_pre_ping = True (mandatory for cloud-hosted databases)

✅ get_db_session() → try...yield...except...finally
   └─ finally: session.close() — সবসময়, exception হলেও

✅ :memory: SQLite → pool_size/max_overflow/pool_timeout নিষিদ্ধ
✅ /health/db/pool → telemetry probe, O(1) metric reads
✅ checked_out_connections == 0 after concurrent load → Zero Leak Contract
```

> **গোল্ডেন রুল:** Connection pool হলো ডেটাবেস রিসোর্সের "bounded buffer"। এটি সংজ্ঞায় bounded — কখনো unbounded হতে দেওয়া যাবে না। `finally: session.close()` হলো সেই বাঁধের দরজা।
