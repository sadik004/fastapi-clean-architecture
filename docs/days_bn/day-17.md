# Day 17: Alembic দিয়ে স্বয়ংক্রিয় ডেটাবেস স্কিমা মাইগ্রেশন — `Base.metadata` দূষণ ও SQLite Batch Alter সমস্যার সমাধান

---

## ১. বাস্তব জীবনের গল্প ও উপমা (Real-World Analogy)

কল্পনা করুন একটি বিশাল অফিস বিল্ডিং যেখানে হাজারো কর্মীর ফাইল-ক্যাবিনেট আছে। প্রতিবার যখন নতুন ধরনের ফর্ম চালু হয়, কোনো ফিল্ড যোগ হয়, বা পুরনো ফিল্ড বাদ যায় — সেই পরিবর্তন সমস্ত ফাইল-ক্যাবিনেটে সুনিপুণভাবে প্রয়োগ করতে হয়।

যদি কেউ বলে "শুধু নতুন ফর্ম তৈরি করো, পুরনো ক্যাবিনেট ভেঙে নতুন করো" — এটা বিপর্যয়কর। সব পুরনো ডেটা নষ্ট হয়ে যাবে।

**Alembic** হলো সেই দক্ষ অফিস ম্যানেজার যিনি:
- পুরনো ফাইল-ক্যাবিনেট (production database) অক্ষত রেখে
- শুধু প্রয়োজনীয় পরিবর্তন (ALTER TABLE) করেন
- প্রতিটি পরিবর্তনের একটি numbered revision (version) নথি রাখেন
- প্রয়োজনে পুরনো অবস্থায় ফিরিয়ে আনেন (downgrade)

`Base.metadata.create_all()` হলো সেই অফিস ম্যানেজার যিনি বলেন "পুরনো সব ক্যাবিনেট ভেঙে নতুন করো" — production-এ একেবারেই গ্রহণযোগ্য নয়।

---

## ২. এটা না বানালে কী মহাবিপদ হতো? (The Production Disaster Without It)

**`Base.metadata.create_all()` production-এ ব্যবহার করলে:**

```python
# app/main.py এ এই বিপজ্জনক কোড ছিল:
@asynccontextmanager
async def lifespan(app: FastAPI):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)  # ← DANGER!
    yield
```

**কী হতো:**
1. **শুধু নতুন টেবিল তৈরি হয়:** পুরনো টেবিল modify হয় না। যদি `UserModel`-এ নতুন column যোগ করো, production database-এ সেটা কখনো যোগ হবে না — silent failure।
2. **Schema drift:** code আর database মিলছে না, SQLAlchemy column access করতে গেলে `ProgrammingError: column does not exist`।
3. **Rollback অসম্ভব:** `create_all()` দিয়ে তৈরি schema downgrade করার কোনো পথ নেই।
4. **Zero audit trail:** কে, কখন, কোন পরিবর্তন করেছে — কিছুই জানা যাবে না।

**সঠিক আর্কিটেকচার:**
```python
# production lifespan: শুধু connectivity probe
async with engine.begin() as conn:
    await conn.execute(text("SELECT 1"))  # ← শুধু connect হওয়া আছে কিনা দেখো

# DDL lifecycle → Alembic-এর হাতে
# alembic upgrade head → সব schema পরিবর্তন নিরাপদে
```

---

## ৩. আর্কিটেকচারাল ডিজাইন ও ইঞ্জিন কীভাবে কাজ করে (System Design)

### Alembic Migration DAG (Directed Acyclic Graph)

```
[base]
  │
  ▼
[e25bf437c78f] ← "create_users_table"
  │  upgrade()  → CREATE TABLE users (...)
  │  downgrade() → DROP TABLE users
  │
  ▼
[6bd9533b08d1] ← "create_posts_table_and_relationship"
  │  upgrade()  → CREATE TABLE posts (...)
  │  downgrade() → DROP TABLE posts
  │
  ▼
[head]  ← সর্বশেষ state
```

প্রতিটি revision একটি **immutable snapshot** — version control (git)-এ tracked। `alembic upgrade head` মানে সব pending revision chronologically apply করো।

### Schema Drift Detection Engine

```python
# compare_metadata() → diff algorithm
alembic.autogenerate.compare_metadata(
    migration_context,  # current database state
    Base.metadata       # our Python model definitions
)
# Returns: [] if in sync, or list of differences
```

এটি **automated drift detection** — CI/CD-এ চালালে কেউ migration ছাড়া model পরিবর্তন করলে test fail করে।

---

## ৪. আমাদের প্রজেক্টের আসল কোড ও লাইনের সহজ ব্যাখ্যা (Real Code Breakdown)

### `app/models/user.py` — SQLAlchemy 2.0 Model

```python
from sqlalchemy.orm import Mapped, mapped_column
from app.core.database import Base

class UserModel(Base):
    __tablename__ = "users"

    # Mapped[int] = SQLAlchemy 2.0 strict typing
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)

    # unique=True + index=True → email দিয়ে O(log N) lookup
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    username: Mapped[str] = mapped_column(String(50), unique=True, index=True, nullable=False)

    # server_default=func.now() → database সার্ভারেই timestamp সেট হয়
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),  # ← UPDATE হলে auto-refresh
        nullable=False,
    )

    # Defensive lazy="raise" → implicit lazy loading নিষিদ্ধ
    posts: Mapped[list["PostModel"]] = relationship(
        "PostModel",
        back_populates="author",
        lazy="raise",  # ← Day 19-এর N+1 protection
    )
```

**`Mapped[...]` কেন SQLAlchemy 2.0-এ mandatory?**  
Python type checker (mypy) বুঝতে পারে column-এর type। `Mapped[int]` মানে `id` সবসময় `int`, কখনো `None` নয়। `Mapped[Optional[str]]` মানে nullable। Runtime-এ কোনো overhead নেই।

---

### `alembic/env.py` — Async Migration Configuration

```python
# ১. Database URL hardcode নয়, settings থেকে dynamic
config.set_main_option("sqlalchemy.url", get_settings().database_url)

# ২. Production Base.metadata bind করা হয়
target_metadata = Base.metadata

# ৩. SQLite batch mode — ALTER TABLE এর জন্য
context.configure(
    ...,
    render_as_batch=True,  # ← SQLite-এ table recreation করে ALTER simulate করে
)

# ৪. Async event loop থেকে Alembic চালানো
def run_migrations_online():
    connectable = engine_from_config(...)

    def do_run_migrations(connection):
        context.configure(connection=connection, ...)
        with context.begin_transaction():
            context.run_migrations()

    # Thread offload — asyncio event loop এর সাথে conflict এড়ানো
    with connectable.connect() as connection:
        connection.run_callable(do_run_migrations)
```

---

### `app/core/database.py` — Programmatic Migration Helpers

```python
def apply_migrations(alembic_ini_path: str = "alembic.ini", revision: str = "head") -> None:
    """Alembic upgrade প্রোগ্রামাটিক্যালি চালানো।"""
    from alembic import command
    from alembic.config import Config

    alembic_cfg = Config(alembic_ini_path)
    # Dynamic URL — hardcoded connection string নয়
    alembic_cfg.set_main_option("sqlalchemy.url", settings.database_url)
    command.upgrade(alembic_cfg, revision)

def rollback_migration(alembic_ini_path: str = "alembic.ini", revision: str = "-1") -> None:
    """Alembic downgrade প্রোগ্রামাটিক্যালি চালানো।"""
    from alembic import command
    from alembic.config import Config

    alembic_cfg = Config(alembic_ini_path)
    alembic_cfg.set_main_option("sqlalchemy.url", settings.database_url)
    command.downgrade(alembic_cfg, revision)
```

---

## ৫. DSA সহজ ভাষায় বিশ্লেষণ (DSA Complexity Made Simple)

| অপারেশন | Complexity | ব্যাখ্যা |
|---|---|---|
| Email/Username lookup | **O(log N)** | B-Tree index traversal |
| Primary key lookup | **O(1)** | Clustered index |
| Migration revision lookup | **O(1)** | Alembic DAG hash map lookup |
| Model registration in metadata | **O(1)** | Dict registration at import time |
| Schema drift comparison | **O(M)** | M = number of model columns/tables |

**B-Tree Index কেন O(log N)?**  
Database-এর B-Tree index একটি balanced binary search tree-এর মতো। ১০ লক্ষ row-তে lookup মানে মাত্র `log₂(1,000,000) ≈ 20` comparisons। Linear scan O(N) হলে ১০ লক্ষ comparison লাগতো।

---

## ৬. এই দিনের RCA ও বাস্তব ভুল থেকে শিক্ষা (Real RCA Incident)

এই দিনে দুটি বাস্তব production incident ঘটেছিল:

### Incident 1: Metadata Namespace Pollution

**ভুল কোড (`tests/test_database_async_setup.py`):**
```python
from app.core.database import Base  # ← Production Base import!

class SampleTestEntity(Base):       # ← এটা Base.metadata-তে register হলো!
    __tablename__ = "day15_sample_test_entities"
    id: Mapped[int] = mapped_column(primary_key=True)
```

**কী হলো:**  
`Base.metadata` একটি singleton। Python import-এর সময় `SampleTestEntity` production `Base.metadata`-তে register হয়ে গেলো। পরে `test_alembic_no_schema_drift` চালালে Alembic দেখলো database-এ `day15_sample_test_entities` নেই, কিন্তু metadata-তে আছে → drift detected → test fail!

**সমাধান — Isolated TestBase:**
```python
# tests/test_database_async_setup.py
from sqlalchemy.ext.asyncio import AsyncAttrs
from sqlalchemy.orm import DeclarativeBase

# ← সম্পূর্ণ আলাদা Base — production Base.metadata-তে কোনো effect নেই
class Day15TestBase(AsyncAttrs, DeclarativeBase):
    """Isolated test declarative base."""
    pass

class SampleTestEntity(Day15TestBase):  # ← TestBase inherit করে, production Base নয়
    __tablename__ = "day15_sample_test_entities"
    ...
```

**স্থায়ী নিয়ম:** Test-only entities কখনো production `Base`-এর subclass হবে না। সবসময় আলাদা `TestBase` তৈরি করতে হবে।

---

### Incident 2: SQLite Batch Alter Downgrade Error

**ভুল কোড (`alembic/versions/e25bf437c78f_.py`):**
```python
def downgrade() -> None:
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_users_username'))  # ← Error!
        batch_op.drop_index(batch_op.f('ix_users_email'))     # ← Error!
    op.drop_table('users')
```

**কী হলো:**  
SQLite-এ `batch_alter_table` table recreate করে (`CREATE _alembic_tmp_users → DROP users → RENAME`). এই recreation-এর মাঝে index drop করতে গেলে:
```
sqlite3.OperationalError: no such index: ix_users_username
```

**মূল কারণ:** `DROP TABLE` করলে সব index automatically drop হয়ে যায়। আলাদাভাবে index drop করার দরকারই নেই।

**সমাধান:**
```python
def downgrade() -> None:
    """Atomic table drop — সব index cascade হয়ে drop হয়।"""
    op.drop_table('users')  # ← এক লাইনেই যথেষ্ট
```

---

## ৭. টেস্টিং স্ট্র্যাটেজি ও কোয়ালিটি গেট (Testing Strategy)

`tests/test_alembic_migrations.py` — ৪টি critical test:

### Test 1: Bi-directional Lifecycle
```python
async def test_alembic_upgrade_and_downgrade_lifecycle():
    """upgrade head → users table exist; downgrade -1 → users table gone."""
    apply_migrations(revision="head")
    # table exist কিনা verify
    apply_migrations(revision="base")
    # table gone কিনা verify
```

### Test 2: Zero Schema Drift
```python
async def test_alembic_no_schema_drift(async_engine):
    """Production model আর migration state-এর মধ্যে কোনো পার্থক্য নেই।"""
    async with async_engine.connect() as conn:
        context = await conn.run_sync(lambda sync_conn: MigrationContext.configure(sync_conn))
        diffs = compare_metadata(context, Base.metadata)
    assert diffs == []  # ← কোনো drift নেই
```

### Test 3: ORM Roundtrip
```python
async def test_user_model_orm_persistence_roundtrip(async_session):
    """UserModel তৈরি করে save করলে সঠিক data retrieve হয়।"""
    user = UserModel(email="test@example.com", ...)
    async_session.add(user)
    await async_session.commit()
    fetched = await async_session.get(UserModel, user.id)
    assert fetched.email == "test@example.com"
```

**Quality Gate:** 265 passed, 100% pass rate।

---

## ৮. ইন্টারভিউ ও ভাইভা প্রশ্ন (Interview Questions & Model Answers)

**প্রশ্ন ১:** "কেন `Base.metadata.create_all()` production-এ ব্যবহার করা উচিত নয়?"

**মডেল উত্তর:** `create_all()` শুধু নতুন টেবিল তৈরি করে, বিদ্যমান টেবিল modify করে না। Production database-এ নতুন column যোগ করলে `create_all()` সেটা apply করবে না — silent schema drift। Alembic migration-এ প্রতিটি পরিবর্তন version-controlled, auditable, এবং reversible।

---

**প্রশ্ন ২:** "`render_as_batch=True` Alembic-এ কেন দরকার?"

**মডেল উত্তর:** SQLite-এ ALTER TABLE সীমিত — column rename বা drop সরাসরি সম্ভব নয়। `render_as_batch=True` থাকলে Alembic একটি temporary table তৈরি করে, data copy করে, পুরনো table drop করে, নতুনটির নাম রাখে। PostgreSQL-এ এটি native ALTER TABLE ব্যবহার করে। এই flag একটি database-agnostic compatibility layer।

---

**প্রশ্ন ৩:** "Test file-এ production `Base` inherit করলে কী সমস্যা হয়?"

**মডেল উত্তর:** `Base.metadata` একটি process-global singleton। Test model `Base` inherit করলে সে production `Base.metadata`-তে register হয়। পরে `compare_metadata()` চালালে সেই test table database-এ না থাকায় schema drift দেখাবে এবং test fail করবে। সমাধান হলো আলাদা `TestBase(AsyncAttrs, DeclarativeBase)` তৈরি করা।

---

**প্রশ্ন ৪:** "Alembic revision DAG এর সুবিধা কী?"

**মডেল উত্তর:** DAG structure মানে প্রতিটি migration তার পূর্ববর্তী revision-কে reference করে। এটি নিশ্চিত করে: (১) chronological application order, (২) multiple parallel migrations-এর safe merging, (৩) যেকোনো point পর্যন্ত upgrade বা downgrade করার ক্ষমতা। Alembic `O(1)` hash lookup দিয়ে revision খোঁজে।

---

## ৯. এক নজরে আসল মূল লজিক (Junior Architect's Operational Checklist)

```
✅ production lifespan → Base.metadata.create_all() নিষিদ্ধ
✅ production lifespan → শুধু SELECT 1 connectivity probe
✅ সব DDL → alembic upgrade head

✅ alembic/env.py → database URL hardcode নয়, get_settings() থেকে dynamic
✅ target_metadata = Base.metadata → সব production model bound
✅ render_as_batch=True → SQLite/PostgreSQL উভয়ে কাজ করে

✅ Test entities → আলাদা TestBase, কখনো production Base নয়
✅ downgrade() → op.drop_table() একটাই, আলাদা index drop নয়

✅ test_alembic_no_schema_drift → CI/CD-এ mandatory
✅ bi-directional reversibility → upgrade ↔ downgrade সবসময় test করতে হবে
```

> **গোল্ডেন রুল:** Alembic migration হলো database-এর git history। প্রতিটি schema পরিবর্তন একটি commit — reversible, auditable, and deployable. `create_all()` হলো "rm -rf" — কখনো production-এ নয়।
