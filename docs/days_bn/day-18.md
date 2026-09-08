# Day 18: রিপোজিটরি প্যাটার্ন — SQLAlchemy 2.0 Async Repository ও "Zero ORM Leakage" আর্কিটেকচারাল বাউন্ডারি

---

## ১. বাস্তব জীবনের গল্প ও উপমা (Real-World Analogy)

কল্পনা করুন একটি বিশাল গ্রন্থাগার। গ্রন্থাগারের ভেতরে বইগুলো বিশেষ ধাতব তাকে সাজানো — এই তাকগুলো শুধু গ্রন্থাগারের ভেতরে কাজ করে, বাইরে আনলে ভেঙে যায়।

পাঠক যখন বই চায়, গ্রন্থাগারিক (Librarian — আমাদের `SqlAlchemyUserRepository`) ভেতরে গিয়ে ধাতব তাক থেকে বই এনে একটি **সাধারণ ব্যাগে** ভরে দেন। পাঠক সেই ব্যাগ নিয়ে যান — তাকটি গ্রন্থাগারের বাইরে যায় না।

এই "সাধারণ ব্যাগ" হলো আমাদের `UserEntity` (Python dataclass) — সহজ, হালকা, session-মুক্ত। আর ধাতব তাক হলো `UserModel` (SQLAlchemy ORM object) — database session-এর সাথে শেকড়ে বাঁধা, বাইরে আনলে `DetachedInstanceError` বা `MissingGreenlet` ক্র্যাশ হয়।

এই "বই ব্যাগে দেওয়ার" কাজটাই হলো `_to_entity()` method — **Zero ORM Leakage Boundary**।

---

## ২. এটা না বানালে কী মহাবিপদ হতো? (The Production Disaster Without It)

**ORM object-কে সরাসরি Service layer-এ পাঠালে কী হতো:**

```python
# বিপজ্জনক আর্কিটেকচার (আমাদের প্রজেক্টে নেই!)
class BadRepository:
    async def get_by_id(self, user_id: int) -> UserModel:  # ← ORM object return!
        result = await self._session.execute(...)
        return result.scalar_one()  # ← session-এর সাথে connected

# Service layer-এ:
user_model = await repo.get_by_id(1)
# ... session বন্ধ হয়ে যায় ...
user_model.email  # ← CRASH! DetachedInstanceError বা MissingGreenlet!
```

**`expire_on_commit=True` (default) হলে আরো ভয়াবহ:**  
Commit-এর পরে SQLAlchemy সব attributes "expire" করে দেয়। পরে `user.email` access করলে সে lazy load করতে চায় → async context-এ synchronous I/O → `MissingGreenlet` crash → HTTP 500।

**আর যদি ORM object router পর্যন্ত পৌঁছায়:**
```python
# Router-এ:
@router.get("/users/{user_id}")
async def get_user(user_id: int, repo = Depends(...)):
    user = await repo.get_by_id(user_id)
    return user  # ← Pydantic serialization-এ lazy load trigger → CRASH!
```

আমাদের `_to_entity()` এই সম্পূর্ণ শ্রেণির বিপদ পুরোপুরি নির্মূল করে।

---

## ৩. আর্কিটেকচারাল ডিজাইন ও ইঞ্জিন কীভাবে কাজ করে (System Design)

### Repository Pattern: Clean Architecture-এ Layer Boundaries

```
┌─────────────────────────────────────────────────┐
│              HTTP Router Layer                   │
│  UserCreate DTO → UserResponse DTO               │
│  (কোনো ORM model জানে না)                       │
└───────────────────────┬─────────────────────────┘
                        │ UserEntity (dataclass)
┌───────────────────────▼─────────────────────────┐
│              Service Layer                       │
│  Business logic: duplicate check, password hash │
│  (কোনো SQL, কোনো ORM model জানে না)            │
└───────────────────────┬─────────────────────────┘
                        │ UserEntity (dataclass)
┌───────────────────────▼─────────────────────────┐
│         Repository Layer (THE WALL)              │
│  UserRepositoryProtocol (abstract interface)     │
│  ├── InMemoryUserRepository (test)               │
│  └── SqlAlchemyUserRepository (production)       │
│       └── _to_entity(): UserModel → UserEntity   │  ← BOUNDARY
└───────────────────────┬─────────────────────────┘
                        │ SQL queries
┌───────────────────────▼─────────────────────────┐
│              Database (SQLite/PostgreSQL)         │
└─────────────────────────────────────────────────┘
```

**Dependency Inversion Principle (DIP):** Service layer নির্দিষ্ট `SqlAlchemyUserRepository` জানে না — শুধু `UserRepositoryProtocol` interface জানে। তাই test-এ `InMemoryUserRepository` inject করলেই service কোনো পরিবর্তন ছাড়া কাজ করে।

---

## ৪. আমাদের প্রজেক্টের আসল কোড ও লাইনের সহজ ব্যাখ্যা (Real Code Breakdown)

### `app/repositories/sqlalchemy_user_repository.py` — The Zero ORM Leakage Boundary

```python
class SqlAlchemyUserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session: AsyncSession = session
        # Session inject হয় — repository নিজে session তৈরি করে না!

    @staticmethod
    def _to_entity(model: UserModel) -> UserEntity:
        """
        ORM Model → Pure Domain Entity রূপান্তর।
        এটি repository-র boundary — এই method-এর বাইরে UserModel কখনো যায় না।
        """
        created_at = model.created_at
        # Timezone-aware নিশ্চিত করা (SQLite naive datetime সমস্যা)
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)

        return UserEntity(
            id=model.id,
            email=model.email,
            username=model.username,
            password_hash=model.password_hash,
            is_active=model.is_active,
            created_at=created_at,
            age=model.age,
            role=model.role,
            full_name=model.full_name,
            phone_number=model.phone_number,
            bio=model.bio,
            company_name=model.company_name,
        )
        # ← এই return-এর পরে model (ORM object) আর ব্যবহার হয় না
```

**`@staticmethod` কেন?**  
`_to_entity` এর কোনো `self` state দরকার নেই — শুধু একটি `UserModel` নিয়ে একটি `UserEntity` দেয়। `@staticmethod` করলে `cls._to_entity(model)` বা `self._to_entity(model)` উভয়ে call করা যায়।

---

### CREATE Operation — flush + refresh pattern

```python
async def create(self, email: str, username: str, password_hash: str, ...) -> UserEntity:
    model = UserModel(
        email=email,
        username=username,
        password_hash=password_hash,
        is_active=True,
        ...
    )
    self._session.add(model)  # ← session-এ stage করা, database-এ নয়
    await self._session.flush()   # ← SQL INSERT চালানো (transaction-এর মধ্যে)
    await self._session.refresh(model)  # ← server_default values load করা
    return self._to_entity(model)  # ← ORM object → pure dataclass
```

**`flush()` vs `commit()` পার্থক্য:**
- `flush()`: SQL INSERT/UPDATE চালায়, কিন্তু transaction চলতে থাকে। Rollback সম্ভব।
- `commit()`: Transaction চূড়ান্ত করে। Rollback অসম্ভব।

**Repository কখনো `commit()` ডাকে না!** Transaction lifetime Unit of Work (Day 20)-এর দায়িত্ব।

---

### LIST Operation — Database-level Pagination

```python
async def list_all(
    self,
    limit: int = 10,
    offset: int = 0,
    role: Optional[str] = None,
    search: Optional[str] = None,
) -> list[UserEntity]:
    stmt = select(UserModel)

    # Filter conditions — SQL WHERE clause
    if role is not None:
        stmt = stmt.where(UserModel.role == role)

    if search is not None and search.strip():
        term = f"%{search.strip().lower()}%"
        stmt = stmt.where(
            or_(
                func.lower(UserModel.username).like(term),
                func.lower(UserModel.email).like(term),
                func.lower(UserModel.full_name).like(term),
            )
        )

    # Database-level pagination → Python heap-এ সব row আসে না!
    stmt = stmt.order_by(UserModel.id.asc()).limit(limit).offset(offset)
    result = await self._session.execute(stmt)
    models = result.scalars().all()
    return [self._to_entity(m) for m in models]
```

**`LIMIT/OFFSET` কেন Python slicing `[offset:offset+limit]`-এর চেয়ে ভালো?**  
Python slicing-এ সব row প্রথমে Python heap-এ আসে তারপর slice নেওয়া হয় — **O(N) memory**। `LIMIT/OFFSET` database engine-এই করে — Python heap-এ শুধু `limit`টি row আসে — **O(limit) memory**।

---

### `app/core/dependencies.py` — Dependency Injection

```python
async def get_user_repository(
    session: Annotated[Optional[AsyncSession], Depends(get_db_session)] = None,
) -> UserRepositoryProtocol:
    """
    Production-এ: SqlAlchemyUserRepository inject হয়
    Test-এ: app.dependency_overrides দিয়ে InMemoryUserRepository inject হয়
    Service layer জানে না পার্থক্য!
    """
    if session is None:
        return InMemoryUserRepository()
    return SqlAlchemyUserRepository(session=session)
```

**Dual-mode DI:** production-এ database session আসে, test-এ `dependency_overrides`-এ InMemory replace করা হয়। Service-এ একটি লাইনও পরিবর্তন না করে।

---

## ৫. DSA সহজ ভাষায় বিশ্লেষণ (DSA Complexity Made Simple)

| অপারেশন | Complexity | Mechanism |
|---|---|---|
| `get_by_id()` | **O(1)** | Clustered primary key index |
| `get_by_email()` | **O(log N)** | Unique B-Tree index on email |
| `get_by_username()` | **O(log N)** | Unique B-Tree index on username |
| `list_all()` | **O(limit)** | DB-level LIMIT/OFFSET |
| `_to_entity()` | **O(1)** | Field-by-field copy, fixed fields |
| `create()` | **O(1)** | Clustered index INSERT |
| `delete()` | **O(1)** | Primary key lookup + DELETE |

**কেন `get_by_email` O(log N) কিন্তু O(1) নয়?**  
B-Tree index-এ `email` column sorted. Binary search করে `log₂(N)` comparisons লাগে। Primary key clustered index-এ row directly addressable — O(1)। Dictionary (`dict`)-এ আমরা O(1) পাই কারণ hash function দিয়ে সরাসরি address বের করা যায়, কিন্তু database B-Tree-এ hash collision handling complex হওয়ায় B-Tree prefer করা হয়।

---

## ৬. এই দিনের RCA ও বাস্তব ভুল থেকে শিক্ষা (Real RCA Incident)

**এই দিনের সবচেয়ে গুরুত্বপূর্ণ architectural lesson** ছিল `expire_on_commit` এবং ORM leakage-এর সংযোগ:

**পরিস্থিতি:**  
`expire_on_commit=True` (default) রেখে ORM object return করলে:

```python
# Service layer-এ:
user_model = await repo.create(...)  # commit হয়
# → expire_on_commit=True → user_model.email expired!

return user_model.email  # ← MissingGreenlet crash!
# কারণ: lazy reload চাই, কিন্তু async context নেই
```

**দুটি সমাধানের মধ্যে আমরা সেরাটা বেছে নিয়েছি:**

1. `expire_on_commit=False` রাখো (Day 15-এ ইতিমধ্যে করা) — ORM object commit-এর পরেও in-memory values রাখে।
2. `_to_entity()` দিয়ে commit-এর আগেই pure dataclass বানাও — ORM dependency একেবারে কেটে দাও।

**আমাদের প্রজেক্টে উভয়ই আছে** — "belt and suspenders" defense:
- `expire_on_commit=False` → database session layer protection
- `_to_entity()` → architectural boundary protection

**পরীক্ষা যা এটি verify করে:**
```python
async def test_sqlalchemy_repo_zero_orm_leakage_boundary(async_session):
    """UserModel session বন্ধ হওয়ার পরেও UserEntity access করা যায়।"""
    user = await repo.create(email="test@example.com", ...)
    await async_session.close()  # ← session বন্ধ

    # UserEntity (dataclass) session-এর উপর নির্ভর করে না
    assert user.email == "test@example.com"  # ← কোনো crash নেই!
```

---

## ৭. টেস্টিং স্ট্র্যাটেজি ও কোয়ালিটি গেট (Testing Strategy)

`tests/test_sqlalchemy_user_repository.py` — ৯টি exhaustive test:

### Test Architecture
```python
@pytest_asyncio.fixture
async def repo(async_session: AsyncSession) -> SqlAlchemyUserRepository:
    """প্রতিটি test-এ fresh session সহ fresh repository।"""
    return SqlAlchemyUserRepository(session=async_session)
```

### Key Tests

**Test: Zero ORM Leakage**
```python
async def test_sqlalchemy_repo_zero_orm_leakage_boundary(repo, async_session):
    user_entity = await repo.create(email="boundary@test.com", ...)
    await async_session.close()

    # Verify: UserEntity is a pure dataclass, session-independent
    assert isinstance(user_entity, UserEntity)
    assert user_entity.email == "boundary@test.com"
    # MissingGreenlet বা DetachedInstanceError নেই!
```

**Test: End-to-End API with Real DB**
```python
async def test_end_to_end_api_crud_with_database(db_client):
    """HTTP endpoint → Router → Service → SqlAlchemyRepository → SQLite database।"""
    # Create
    res = await db_client.post("/users/", json={...})
    assert res.status_code == 201
    user_id = res.json()["id"]

    # Read
    res = await db_client.get(f"/users/{user_id}")
    assert res.status_code == 200
    assert res.json()["email"] == "..."

    # Update
    res = await db_client.patch(f"/users/{user_id}", json={"full_name": "Updated"})
    assert res.status_code == 200

    # Delete
    res = await db_client.delete(f"/users/{user_id}")
    assert res.status_code == 204
```

**Quality Gate:** 274 passed, 100% pass rate।

---

## ৮. ইন্টারভিউ ও ভাইভা প্রশ্ন (Interview Questions & Model Answers)

**প্রশ্ন ১:** "Repository Pattern কেন ব্যবহার করেন?"

**মডেল উত্তর:** Repository Pattern তিনটি সুবিধা দেয়: (১) Dependency Inversion — Service layer কোনো নির্দিষ্ট database জানে না, শুধু interface জানে; (২) Testability — InMemory implementation দিয়ে database ছাড়াই unit test; (৩) Portability — PostgreSQL থেকে MongoDB-তে migrate করতে শুধু Repository implementation পরিবর্তন।

---

**প্রশ্ন ২:** "Zero ORM Leakage Boundary মানে কী?"

**মডেল উত্তর:** SQLAlchemy ORM objects (`UserModel`) active database session-এর সাথে bound। এগুলো async context-এর বাইরে গেলে `MissingGreenlet` বা `DetachedInstanceError` crash দেয়। `_to_entity()` ORM object-কে session-independent pure Python dataclass (`UserEntity`)-এ রূপান্তর করে। Repository boundary-র বাইরে কখনো ORM object যায় না।

---

**প্রশ্ন ৩:** "Repository কি `commit()` করে?"

**মডেল উত্তর:** না — কখনো না। Repository শুধু `flush()` করে (SQL execute, transaction জারি)। `commit()` করার দায়িত্ব Unit of Work (UoW)-এর। এই separation নিশ্চিত করে একটি UoW-এর ভেতরে একাধিক repository operation atomically commit বা rollback করা যায়।

---

**প্রশ্ন ৪:** "DB-level pagination আর Python slicing-এর পার্থক্য?"

**মডেল উত্তর:** Python slicing (`list[offset:offset+limit]`) সব N row Python heap-এ load করে তারপর slice নেয় — O(N) memory। `LIMIT limit OFFSET offset` database engine-ই করে — Python heap-এ শুধু `limit`টি row আসে — O(limit) memory। ১০ লক্ষ user-এ page 5 দেখাতে Python slicing হলে ১০ লক্ষ UserEntity RAM-এ আসে। DB pagination-এ শুধু ১০টি আসে।

---

## ৯. এক নজরে আসল মূল লজিক (Junior Architect's Operational Checklist)

```
✅ Repository → Protocol (interface) expose করে, implementation লুকায়
✅ Service → শুধু Protocol জানে, কোনো ORM import নেই
✅ Router → শুধু DTO জানে, কোনো Entity/Model import নেই

✅ _to_entity() → ORM object → dataclass, ALWAYS before return
✅ flush() ব্যবহার করো, commit() নয় — UoW-এর জন্য রেখে দাও
✅ refresh() → server_default values (timestamp, autoincrement ID) reload

✅ get_by_id → O(1) clustered index
✅ get_by_email → O(log N) B-Tree index  
✅ list_all → DB-level LIMIT/OFFSET, Python heap-এ শুধু `limit` rows

✅ expire_on_commit=False → database layer protection
✅ _to_entity() → architectural boundary protection
   (উভয়ই একসাথে = "belt and suspenders" defense)
```

> **গোল্ডেন রুল:** `_to_entity()` হলো repository-র "국경 পুলিশ" — কোনো ORM object সীমানা পার হতে পারবে না। যেদিন এই নিয়ম ভাঙবে, সেদিন production-এ `MissingGreenlet` crash হবে এবং ব্যবহারকারীরা রাত ৩টায় HTTP 500 দেখবে।
