# Day 20: Unit of Work (UoW) প্যাটার্ন — একাধিক রিপোজিটরি জুড়ে পারমাণবিক ACID ট্রানজেকশন

---

### 📌 ব্যবহৃত DSA-এর নাম (Exact Data Structure & Algorithm Name)
- **Unit of Work Behavioral Pattern, Transaction Context Graph, and In-Memory State Snapshot**: একাধিক রিপোজিটরি জুড়ে সিঙ্গেল ট্রানজ্যাকশন বাউন্ডারি এবং টেস্টিংয়ের জন্য অগভীর কপি (`dict()` snapshot)।

### 🚀 প্রোডাকশনে ঠিক কখন ব্যবহার করব? (When to use in Production)
- মাল্টি-টেবিল পারমাণবিক লেনদেন (ইউজার তৈরি ও সাথে সাথে ওয়েলকাম পোস্ট তৈরি, ব্যাংকের অ্যাকাউন্ট ডেবিট ও ক্রেডিট লেনদেন)।

### 🎯 কী কারণে বা কোন পরিস্থিতিতে ব্যবহার করব? (Why to use / Technical Triggers)
- **পার্শিয়াল রাইট বা অরফানড রেকর্ড (Orphaned Record) বিপর্যয় প্রতিরোধ**: প্রথম টেবিলে ডেটা সেভ হওয়ার পর দ্বিতীয় অপারেশনে এরর হলে প্রথম ডেটাটি ডাটাবেসে এতিম হয়ে পড়ে থাকে; UoW নিশ্চিত করে হয় উভয়ই সফলভাবে কমিট হবে, নয়তো সম্পূর্ণ রোলব্যাক হয়ে জিরো অরফানড রেকর্ড থাকবে।

---

## ১. বাস্তব জীবনের গল্প ও উপমা (Real-World Analogy)

কল্পনা করুন একটি ব্যাংকের লেনদেন সিস্টেম। আপনি আপনার অ্যাকাউন্ট থেকে বন্ধুর অ্যাকাউন্টে ৫,০০০ টাকা পাঠাচ্ছেন। এই লেনদেনে দুটি ধাপ আছে:
1. আপনার অ্যাকাউন্ট থেকে ৫,০০০ টাকা কাটো
2. বন্ধুর অ্যাকাউন্টে ৫,০০০ টাকা যোগ করো

এখন কল্পনা করুন ধাপ ১ সম্পন্ন হওয়ার পরে মাঝপথে বিদ্যুৎ চলে গেলো — আপনার ৫,০০০ টাকা গেলো, বন্ধুর অ্যাকাউন্টে আসলো না। এটাই **Partial Write** বা **Orphaned Record** বিপদ।

**Unit of Work** হলো সেই ব্যাংক ম্যানেজার যিনি বলেন:
> "উভয় ধাপ সফল হলেই শুধু ledger-এ চূড়ান্ত করবো (commit)। যদি কোনো একটি ব্যর্থ হয়, উভয়ই বাতিল করবো (rollback)।"

আমাদের প্রজেক্টে: User registration + Initial Post creation — **উভয়ই সফল হলে commit, অন্যথায় কোনোটাই থাকবে না।**

---

## ২. এটা না বানালে কী মহাবিপদ হতো? (The Production Disaster Without It)

**UoW ছাড়া প্রতিটি repository আলাদাভাবে commit করলে:**

```python
# বিপজ্জনক pattern (আমাদের প্রজেক্টে নেই!):
async def create_user_with_initial_post(self, ...):
    user = await self.user_repo.create(...)    # ← commit 1: user saved!
    # এখানে exception হলো (duplicate post, network error, ইত্যাদি)
    post = await self.post_repo.create(...)    # ← কখনো পৌঁছায় না
    # Result: user আছে, post নেই → orphaned user!
```

**Production-এ পরিণতি:**
- ১০,০০০ orphaned user records ডেটাবেসে জমা হয়
- Email "already registered" বললেও ব্যবহারকারী login করতে পারে না (post নেই)
- Database integrity violation
- Manual cleanup script লিখতে DBA টিম ৩ ঘণ্টা কাজ করে

**ACID গ্যারান্টি UoW-এ:**
- **A**tomicity: হয় সব হবে, নয় কিছুই না
- **C**onsistency: Database সবসময় valid state-এ থাকবে
- **I**solation: চলমান transaction অন্য transaction থেকে isolated
- **D**urability: commit হলে crash-এও data থাকবে

---

## ৩. আর্কিটেকচারাল ডিজাইন ও ইঞ্জিন কীভাবে কাজ করে (System Design)

### Unit of Work Architecture

```
┌─────────────────────────────────────────────────────────┐
│                   Service Layer                          │
│   async with uow:                                        │
│       user = await uow.users.create(...)   ─────────┐   │
│       post = await uow.posts.create(...)   ─────────┤   │
│       # সফল হলে → implicit commit                  │   │
│       # exception হলে → automatic rollback         │   │
└──────────────────────────────────────────────────────┘   │
                         │                                  │
                         ▼                                  │
┌──────────────────────────────────────────────────────┐   │
│              SqlAlchemyUnitOfWork                     │   │
│                                                       │   │
│  self.session ←────────────────────────────────────┘  │
│       ↑                        ↑                      │
│  SqlAlchemyUserRepository   SqlAlchemyPostRepository  │
│  (SAME session!)            (SAME session!)           │
│                                                       │
│  __aenter__: session = async_session_factory()        │
│  __aexit__:  rollback() if exception, close() always  │
└───────────────────────┬───────────────────────────────┘
                        │ SINGLE connection
                        ▼
              [Database: users + posts tables]
```

**Critical Insight:** User repository আর Post repository **একটিই** `AsyncSession` share করে। তাই commit হলে উভয়ের changes একসাথে persist হয়, rollback হলে উভয়ের changes একসাথে বাতিল হয়।

---

## ৪. আমাদের প্রজেক্টের আসল কোড ও লাইনের সহজ ব্যাখ্যা (Real Code Breakdown)

### `app/core/unit_of_work.py` — UnitOfWorkProtocol

```python
class UnitOfWorkProtocol(Protocol):
    """Abstract interface — implementation জানা দরকার নেই।"""

    @property
    def users(self) -> UserRepositoryProtocol:
        """User repository on shared transaction."""
        ...

    @property
    def posts(self) -> PostRepositoryProtocol:
        """Post repository on shared transaction."""
        ...

    async def __aenter__(self) -> Self: ...
    async def __aexit__(self, ...) -> None: ...
    async def commit(self) -> None: ...
    async def rollback(self) -> None: ...
```

**কেন `@property` দিয়ে Protocol member declare করা হলো?**  
এটা Day 20-এর একটি গুরুত্বপূর্ণ RCA শিক্ষা (বিস্তারিত Section 6-এ)। Plain annotation `users: UserRepositoryProtocol` দিলে Mypy `@property` implementation reject করে। `@property`-তে declare করলে উভয়ই accept হয়।

---

### `app/core/unit_of_work.py` — SqlAlchemyUnitOfWork

```python
class SqlAlchemyUnitOfWork:

    def __init__(self, session_factory = None) -> None:
        self._session_factory = session_factory or async_session_factory
        self.session: Optional[AsyncSession] = None
        self._users: Optional[SqlAlchemyUserRepository] = None
        self._posts: Optional[SqlAlchemyPostRepository] = None

    @property
    def users(self) -> UserRepositoryProtocol:
        if self._users is None:
            raise RuntimeError("UoW not open. Use 'async with uow:' block.")
        return self._users

    @property
    def posts(self) -> PostRepositoryProtocol:
        if self._posts is None:
            raise RuntimeError("UoW not open. Use 'async with uow:' block.")
        return self._posts

    async def __aenter__(self) -> Self:
        # ১. Session তৈরি
        self.session = self._session_factory()
        # ২. SAME session উভয় repository-তে inject
        self._users = SqlAlchemyUserRepository(session=self.session)
        self._posts = SqlAlchemyPostRepository(session=self.session)
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        try:
            if exc_type is not None:
                await self.rollback()  # ← exception হলে সব বাতিল
            # exception না হলে → caller-এর implicit commit বা explicit commit
        finally:
            if self.session is not None:
                await self.session.close()  # ← সবসময় close, leak নেই
                self.session = None
                self._users = None
                self._posts = None
```

**`__aexit__` এর `try...finally` কেন critical?**  
`rollback()` নিজেও exception throw করতে পারে (যেমন database connection drop)। `finally` নিশ্চিত করে session সবসময় close হয় — rollback-এ exception হলেও। Connection pool leak সম্পূর্ণ নিষিদ্ধ।

---

### `app/core/unit_of_work.py` — InMemoryUnitOfWork (Testing)

```python
class InMemoryUnitOfWork:
    """Snapshot-based rollback — test-এ real database ছাড়াই ACID simulation।"""

    async def __aenter__(self) -> Self:
        # context enter-এ state snapshot নেওয়া হয়
        self._user_store_snapshot = dict(self.users._store)
        self._user_email_snapshot = dict(self.users._email_index)
        self._user_username_snapshot = dict(self.users._username_index)
        self._user_id_snapshot = self.users._current_id

        self._post_store_snapshot = dict(self.posts._store)
        self._post_id_snapshot = self.posts._current_id
        return self

    async def rollback(self) -> None:
        # Snapshot থেকে restore করা
        if self._user_store_snapshot is not None:
            self.users._store = dict(self._user_store_snapshot)
        if self._user_email_snapshot is not None:
            self.users._email_index = dict(self._user_email_snapshot)
        # ... অন্য fields restore ...
```

**Snapshot Pattern কেন?**  
`dict()` shallow copy নেওয়া O(N)। এটি একটি in-memory "transaction log" — rollback-এ আগের state পুনরায় বসানো। Production `SqlAlchemyUnitOfWork`-এর মতো একই API surface, কিন্তু database ছাড়া।

---

### `app/services/user_service.py` — Atomic Service Method

```python
async def create_user_with_initial_post(
    self,
    user_data: UserCreate,
    post_title: str,
    post_content: str,
) -> UserWithInitialPostResponse:

    async with self._uow as uow:  # ← UoW context শুরু
        # ১. User তৈরি
        if await uow.users.get_by_email(user_data.email):
            raise DuplicateEmailError(user_data.email)

        user = await uow.users.create(
            email=user_data.email,
            username=user_data.username,
            password_hash=hash_password(user_data.password),
        )

        # ২. Initial post তৈরি (same transaction!)
        post = await uow.posts.create(
            title=post_title,
            content=post_content,
            user_id=user.id,
        )

        await uow.commit()  # ← উভয়ই একসাথে persist

    # ← UoW context শেষ, session closed
    return UserWithInitialPostResponse(user=user, post=post)
```

**যদি `uow.posts.create(...)` exception throw করে:**
- `__aexit__` call হয় `exc_type is not None` দিয়ে
- `await self.rollback()` → user creation সহ সব বাতিল
- Database-এ কোনো orphaned user নেই!

---

## ৫. DSA সহজ ভাষায় বিশ্লেষণ (DSA Complexity Made Simple)

| অপারেশন | Complexity | ব্যাখ্যা |
|---|---|---|
| UoW context enter (`__aenter__`) | **O(1)** | Session factory call + repo init |
| User creation (staged) | **O(1)** | Clustered index INSERT (buffered) |
| Post creation (staged) | **O(1)** | Clustered index INSERT (buffered) |
| `commit()` | **O(1)** | Single transaction commit |
| `rollback()` | **O(1)** | Transaction abort signal |
| InMemory snapshot (`dict()`) | **O(N)** | N = active entities |
| InMemory rollback restore | **O(N)** | Dict copy restore |

**ACID atomicity এর ভিত্তি — Database Write-Ahead Log (WAL):**  
SQL database WAL-এ প্রতিটি operation log করে। `commit()` মানে WAL-এ "COMMIT" mark। Power failure হলেও restart-এ WAL replay করে committed state restore হয়। `rollback()` মানে WAL-এ "ABORT" mark — uncommitted changes ignore হয়।

---

## ৬. এই দিনের RCA ও বাস্তব ভুল থেকে শিক্ষা (Real RCA Incident)

এই দিনে দুটি বাস্তব failure ঘটেছিল:

### Incident 1: Protocol Settable vs Read-Only Property Mismatch

**Mypy error:**
```
app\core\dependencies.py:48: error: Incompatible return value type
  (got "SqlAlchemyUnitOfWork", expected "UnitOfWorkProtocol")
  note: Protocol member UnitOfWorkProtocol.posts expected settable variable,
        got read-only attribute
```

**ভুল কোড:**
```python
class UnitOfWorkProtocol(Protocol):
    users: UserRepositoryProtocol  # ← Plain annotation = settable variable
    posts: PostRepositoryProtocol  # ← Mypy expects obj.posts = something
```

**কারণ:** PEP 544 অনুযায়ী Protocol-এ plain annotation মানে read-write variable। কিন্তু `SqlAlchemyUnitOfWork`-এ `@property` শুধু getter — setter নেই। Mypy incompatible বলে।

**সমাধান:**
```python
class UnitOfWorkProtocol(Protocol):
    @property
    def users(self) -> UserRepositoryProtocol: ...  # ← read-only property

    @property
    def posts(self) -> PostRepositoryProtocol: ...
```

Python typing-এ Protocol `@property` দিলে implementing class-এ `@property` বা সাধারণ instance variable — উভয়ই accept হয়। `InMemoryUnitOfWork`-এ `self.users = ...` instance variable আছে — এটাও সঠিক।

**স্থায়ী নিয়ম:** Protocol-এ interface member যদি `@property` দিয়ে implement হওয়ার কথা থাকে, Protocol-এও `@property` দিয়ে declare করো।

---

### Incident 2: Error Envelope Test Assertion

**ভুল test assertion:**
```python
assert "already registered" in second_res.json()["message"]  # KeyError!
```

**কারণ:** Day 14-এ আমরা centralized error envelope তৈরি করেছি:
```json
{
  "error": {
    "code": "DUPLICATE_EMAIL",
    "message": "Email already registered",
    "trace_id": "abc123"
  }
}
```

Top-level `"message"` নেই — `error.message` আছে।

**সমাধান:**
```python
error_body = second_res.json()
error_msg = error_body.get("error", {}).get("message", "") or error_body.get("detail", "")
assert "already registered" in error_msg
```

**স্থায়ী নিয়ম:** সব API test-এ error envelope `response.json()["error"]["message"]` path ব্যবহার করো — কখনো top-level `"message"` নয়।

---

## ৭. টেস্টিং স্ট্র্যাটেজি ও কোয়ালিটি গেট (Testing Strategy)

`tests/test_unit_of_work.py` — ৯টি comprehensive test:

### Test 1: Atomic Commit
```python
async def test_uow_atomic_commit_persists_user_and_post(db_uow):
    """UoW commit → user এবং post উভয়ই database-এ থাকে।"""
    async with db_uow as uow:
        user = await uow.users.create(email="test@test.com", ...)
        post = await uow.posts.create(title="Hello", user_id=user.id, ...)
        await uow.commit()

    # UoW বন্ধ হওয়ার পরেও data persist
    fetched_user = await user_repo.get_by_id(user.id)
    assert fetched_user is not None
    assert fetched_user.email == "test@test.com"
```

### Test 2: Rollback on Exception — Zero Orphaned Records
```python
async def test_uow_rollback_on_exception_leaves_zero_orphaned_records(db_uow):
    """Post creation-এ exception → user creation-ও rollback।"""
    try:
        async with db_uow as uow:
            user = await uow.users.create(email="orphan@test.com", ...)
            raise RuntimeError("Simulated failure during post creation!")
    except RuntimeError:
        pass

    # Rollback verification: user exist করে না
    fetched = await user_repo.get_by_email("orphan@test.com")
    assert fetched is None  # ← Zero orphaned record!
```

### Test 3: InMemory Snapshot Rollback
```python
async def test_in_memory_uow_snapshot_and_rollback():
    """InMemoryUoW-এ rollback → pre-context state restore।"""
    uow = InMemoryUnitOfWork()
    async with uow as u:
        await u.users.create(email="temp@test.com", ...)
        await u.rollback()  # explicit rollback

    # State restored to pre-context snapshot
    assert await uow.users.get_by_email("temp@test.com") is None
```

### Test 4: Access Guard
```python
async def test_uow_session_cleanup_and_access_guards(db_uow):
    """UoW বন্ধ হওয়ার পরে uow.users access → RuntimeError।"""
    async with db_uow as uow:
        pass  # context ended

    with pytest.raises(RuntimeError, match="not open"):
        _ = uow.users  # ← GUARD!
```

**Quality Gate:** 287 passed, 100% pass rate।

---

## ৮. ইন্টারভিউ ও ভাইভা প্রশ্ন (Interview Questions & Model Answers)

**প্রশ্ন ১:** "Unit of Work Pattern কী এবং Repository Pattern থেকে কীভাবে আলাদা?"

**মডেল উত্তর:** Repository Pattern একটি entity type-এর CRUD operations encapsulate করে। Unit of Work একাধিক repository-কে একটি shared transaction-এ coordinating করে। Repository জানে না transaction কখন commit বা rollback হবে — UoW সেটা decide করে। এটা Separation of Concerns: Repository = data access, UoW = transaction boundary।

---

**প্রশ্ন ২:** "কেন উভয় Repository-তে same session inject করা জরুরি?"

**মডেল উত্তর:** SQL transaction একটি database connection-এর context-এ থাকে। দুটি আলাদা session মানে দুটি আলাদা transaction — একটির rollback অন্যটিকে affect করে না। Same session share করলে `await session.rollback()` একটি call-এই user এবং post উভয়ের staged changes বাতিল করে। এটাই true atomicity।

---

**প্রশ্ন ৩:** "Repository-তে `commit()` থাকা উচিত কিনা?"

**মডেল উত্তর:** না। Repository শুধু `flush()` করে — SQL execute করে কিন্তু transaction নিজের হাতে রাখে। `commit()` UoW-এর দায়িত্ব। যদি Repository `commit()` করতো, একটি atomic service operation-এ (user + post) partial commits হতো — rollback সম্ভব হতো না। এই separation ACID guarantee-র ভিত্তি।

---

**প্রশ্ন ৪:** "InMemoryUoW test-এ কেন দরকার যদি SqlAlchemyUoW-ই আছে?"

**মডেল উত্তর:** `SqlAlchemyUoW` unit test-এ ব্যবহার করলে database connection দরকার হয় — test slow এবং isolated নয়। `InMemoryUoW` dictionary-based — ultra-fast, database-free। Service-layer business logic test করতে (duplicate email rejection, rollback semantics) `InMemoryUoW` যথেষ্ট। Integration tests-এ `SqlAlchemyUoW` ব্যবহার করো।

---

## ৯. এক নজরে আসল মূল লজিক (Junior Architect's Operational Checklist)

```
✅ Repository → flush() করে, commit() কখনো নয়
✅ UnitOfWork → commit() এবং rollback() কন্ট্রোল করে

✅ SqlAlchemyUoW.__aenter__:
   session = session_factory()
   users = UserRepo(session=self.session)  # SAME session!
   posts = PostRepo(session=self.session)  # SAME session!

✅ SqlAlchemyUoW.__aexit__:
   try:
     if exception → rollback()
   finally:
     session.close()  # সবসময়, exception হলেও

✅ Protocol @property → read-only members-এ @property mandatory
   (plain annotation = settable = Mypy error)

✅ Service: async with uow: → commit-এর আগে কোনো exception → সব rollback
✅ access guard: uow context-এর বাইরে uow.users → RuntimeError

✅ InMemoryUoW → dict() snapshot on enter, restore on rollback
✅ error envelope: response.json()["error"]["message"] সবসময়
```

> **গোল্ডেন রুল:** ACID transactions-এ "হয় সব, নয় কিছুই না" — এটা শুধু একটি ধারণা নয়, এটা code-এ enforce করতে হবে। UoW-এর `__aexit__`-এ `finally: session.close()` হলো সেই enforce — exception হোক বা না হোক, connection pool-এ সংযোগ ফিরে যাবেই। Database কোনো orphaned transaction চালু রাখবে না।
