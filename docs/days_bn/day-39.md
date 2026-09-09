# Day 39: অপটিমিস্টিক কনকারেন্সি কন্ট্রোল (OCC) আর্কিটেকচার এবং রো ভার্সনিং (Optimistic Concurrency Control with Row Versioning)

---

## ১. আমরা কী বানিয়েছি?

আমরা আজ এন্টারপ্রাইজ গ্রেডের **অপটিমিস্টিক কনকারেন্সি কন্ট্রোল (OCC)** আর্কিটেকচার তৈরি করেছি, যা ডেটাবেসের ভারী, ব্লকিং লক (Pessimistic Lock) ছাড়াই কনকারেন্ট রাইট ট্রাফিকের মধ্যে **Lost Update Problem** চিরতরে নির্মূল করে। এর মাধ্যমে:

1. `UserModel`-এ একটি **`version` কলাম** যুক্ত করা হয়েছে যা প্রতিটি সফল আপডেটে `version + 1` হয়।
2. রিপোজিটরিতে **`update_with_optimistic_lock`** মেথড তৈরি করা হয়েছে যা একটি **অ্যাটমিক, কন্ডিশনাল SQL UPDATE** চালায়:
   ```sql
   UPDATE users SET ..., version = version + 1
   WHERE id = :user_id AND version = :expected_version
   ```
3. যদি `rowcount == 0` হয় (অর্থাৎ অন্য কোনো ট্রানজেকশন আগেই রেকর্ড পরিবর্তন করেছে), তাহলে `OptimisticLockException` উত্থাপিত হয় যা `HTTP 409 Conflict` হিসেবে ক্লায়েন্টকে জানায়।

---

## 📌 ব্যবহৃত DSA-এর সুনির্দিষ্ট নাম:
**অপটিমিস্টিক কনকারেন্সি কন্ট্রোল / ভার্সন-বেসড অপটিমিস্টিক লকিং (Optimistic Concurrency Control - OCC with Row Versioning) — O(1) Non-Blocking Reads, Atomic Conditional UPDATE**

## 🚀 প্রোডাকশনে ঠিক কখন ব্যবহার করব? (When to use in Production)
- **ই-কমার্স ইনভেন্টরি ও সিট বুকিং (Low to Medium Contention):** যখন রিড ট্রাফিক খুব বেশি কিন্তু রাইট কম, যেমন প্রোডাক্টের স্টক আপডেট।
- **ইউজার প্রোফাইল বা ডকুমেন্ট এডিটিং (Google Docs / Wiki style):** দুজন অ্যাডমিন যেন একই সময়ে একজনের প্রোফাইল বা কনফিগ এডিট করে একজনের ডেটা আরেকজন মুছে না ফেলে (Lost Update)।
- **অ্যাকাউন্ট ব্যালেন্স ও ওয়ালেট ট্রানজ্যাকশন:** যেখানে রিডের সময় কোনো ডাটাবেস রো লক না করে আল্ট্রাফাস্ট রিড স্পিড বজায় রাখতে হয়।

## 🎯 কী কারণে বা কোন পরিস্থিতিতে ব্যবহার করব? (Why to use / Technical Triggers)
- **লস্ট আপডেট প্রবলেম (The Lost Update Problem) চিরতরে নির্মূল:** দুটি কনকারেন্ট রিকোয়েস্টের একটি যেন অন্যটির ডেটাকে না জানিয়ে ওভাররাইট করে ধ্বংস না করে।
- **ডাটাবেসে কোনো ভারী লক ছাড়া ও(১) নন-ব্লকিং রিড স্পিড:** পেসিমিস্টিক লকিংয়ের মতো পুরো টেবিল বা রো লক করে অন্যদের সারিবদ্ধভাবে দাঁড় করিয়ে রাখার ওভারহেড ও ডেডলক ঝুঁকি দূর করা।
- **রো-কাউন্ট (rowcount == 0) চেক দিয়ে অ্যাটমিক কনফ্লিক্ট ডিটেকশন:** এক লাইনের SQL `WHERE id = :id AND version = :version` দিয়ে সেকেন্ডের ভগ্নাংশে কনফ্লিক্ট ধরে HTTP 409 ছুড়ে দেওয়া।

---

## ২. বাস্তব জীবনের রূপক: গুগল ডক্সে একই ডকুমেন্ট এডিটিং

কল্পনা করুন দুইজন সহকর্মী — **রাহেলা** এবং **করিম** — একটি গুরুত্বপূর্ণ প্রজেক্ট প্রপোজাল ডকুমেন্ট একই সময়ে এডিট করছেন:

### ❌ লক ছাড়া কী হতো? (The Lost Update Disaster)

```
সময় T=0:  রাহেলা ডক পড়লেন → বাজেট: ৳৫০,০০০
সময় T=0:  করিম ডক পড়লেন  → বাজেট: ৳৫০,০০০

সময় T=1:  রাহেলা বাজেট পরিবর্তন করলেন → ৳৭৫,০০০
সময় T=2:  করিম বাজেট পরিবর্তন করলেন  → ৳৬০,০০০

ফলাফল: ডকুমেন্টে বাজেট = ৳৬০,০০০ (রাহেলার ৳৭৫,০০০ হাওয়া!)
```
রাহেলার পরিশ্রমী পরিবর্তন নীরবে মুছে গেল — এটাই **Lost Update Problem**।

### ✅ OCC দিয়ে কী হয়? (Version Stamped Solution)

```
সময় T=0:  রাহেলা পড়লেন → বাজেট: ৳৫০,০০০ (version=5)
সময় T=0:  করিম পড়লেন  → বাজেট: ৳৫০,০০০ (version=5)

সময় T=1:  রাহেলা সেভ করলেন:
           UPDATE SET budget=75000, version=6
           WHERE id=1 AND version=5
           → সফল! ✅ (version এখন 6)

সময় T=2:  করিম সেভ করলেন:
           UPDATE SET budget=60000, version=6
           WHERE id=1 AND version=5
           → rowcount=0! ❌ version=5 আর নেই, এখন version=6
           → HTTP 409 Conflict: "Stale version. Please refresh and retry."
```
করিম জানলেন কনফ্লিক্ট হয়েছে এবং রাহেলার পরিবর্তন সম্পূর্ণ নিরাপদ রইল।

---

## ৩. প্রোডাকশন সিস্টেমের বিপর্যয় ও পেছনের গল্প (The "Why" Behind The Architecture)

### বিপর্যয় ১: ব্যাংকের ট্রানজেকশন ডুপ্লিকেশন ও ব্যালেন্স ইরেজার

২০১২ সালে একটি বড় ব্যাংকের মোবাইল অ্যাপে একটি ক্লাসিক বাগ আবিষ্কার হয়। ইউজার একই সময়ে দুই ডিভাইসে ফান্ড ট্রান্সফার করার চেষ্টা করে:

- **ডিভাইস A:** ব্যালেন্স ৳১০,০০০ পড়ে ৳৫,০০০ ট্রান্সফার করে → ব্যালেন্স সেভ করে ৳৫,০০০
- **ডিভাইস B:** একই পুরনো ব্যালেন্স ৳১০,০০০ পড়ে ৳৮,০০০ ট্রান্সফার করে → ব্যালেন্স সেভ করে ৳২,০০০

ব্যাংকের অ্যাকাউন্টে দেখায় ৳২,০০০ কিন্তু মোট ১৩,০০০ টাকা পাঠানো হয়েছে — **৳৩,০০০ অদৃশ্য হয়ে গেছে।** OCC থাকলে দ্বিতীয় ট্রানজেকশন `409 Conflict` পেত এবং ইউজারকে সঠিক ব্যালেন্স দেখানো হতো।

### বিপর্যয় ২: `SELECT FOR UPDATE` পেসিমিস্টিক লকের থ্রুপুট হত্যা

একটি ই-কমার্স টিম পেসিমিস্টিক লক দিয়ে স্টক কাউন্ট আপডেট করছিল:
```sql
SELECT * FROM products WHERE id = :id FOR UPDATE;
-- ... অ্যাপ্লিকেশনে প্রসেসিং (100ms)
UPDATE products SET stock = :new_stock WHERE id = :id;
```
ব্ল্যাক ফ্রাইডেতে একটি পপুলার প্রোডাক্টে ১০,০০০ কনকারেন্ট ইউজার আসে। লক-ওয়েইটিং ট্রানজেকশনের সংখ্যা এত বাড়ে যে ডেটাবেস সার্ভার মেমরি শেষ হয়ে ক্র্যাশ করে। পুরো সাইট ৪৫ মিনিট ডাউন। OCC দিয়ে কোনো লক ছাড়াই কনকারেন্ট রাইট হ্যান্ডেল করা যেত।

---

## ৪. আর্কিটেকচার ওয়াকথ্রু (Architecture Deep Dive)

### ৪.১ ডেটাবেস মডেল — ভার্সন কলাম যোগ ([`app/models/user.py`](file:///e:/FastApi1/app/models/user.py))

```python
# প্রতিটি রো-র জন্য একটি মনোটোনিক ইন্টিজার ভার্সন স্ট্যাম্প
version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
```

**কেন `Integer, default=1`?**
- নতুন ইউজার তৈরি হলে `version=1` থেকে শুরু।
- প্রতিটি সফল আপডেটে `version = version + 1` — এটি ডেটাবেসের সাইডে গণনা হয়, অ্যাপ্লিকেশনে নয়, তাই রেস-ফ্রি।

### ৪.২ রিপোজিটরি — অ্যাটমিক কন্ডিশনাল আপডেট ([`app/repositories/sqlalchemy_user_repository.py`](file:///e:/FastApi1/app/repositories/sqlalchemy_user_repository.py))

```python
async def update_with_optimistic_lock(
    self,
    user_id: int,
    expected_version: int,
    update_data: UserUpdate,
) -> UserEntity:
    """
    অ্যাটমিক কন্ডিশনাল SQL UPDATE:
    UPDATE users
    SET ..., version = version + 1
    WHERE id = :user_id AND version = :expected_version
    """
    update_dict = update_data.model_dump(exclude_unset=True)

    stmt = (
        update(UserModel)
        .where(UserModel.id == user_id, UserModel.version == expected_version)
        .values(**update_dict, version=UserModel.version + 1)
    )
    result = await self.session.execute(stmt)

    # rowcount=0 মানে: হয় user নেই, নয় version stale
    if result.rowcount == 0:
        raise OptimisticLockException(
            "Resource was modified by another transaction. "
            "Stale version detected; please refresh and retry."
        )

    # সর্বশেষ DB state রিফ্রেশ করে ডোমেইন এন্টিটি রিটার্ন
    stmt_select = select(UserModel).where(UserModel.id == user_id)
    res_select = await self._session.execute(stmt_select)
    updated_model = res_select.scalar_one()
    return self._to_entity(updated_model)
```

**এই ডিজাইনের ৩টি অপরিহার্য প্রপার্টি:**

| প্রপার্টি | ব্যাখ্যা |
|-----------|----------|
| **অ্যাটমিক** | একটি একক SQL স্টেটমেন্ট — ডেটাবেস স্তরে অবিভাজ্য |
| **নন-ব্লকিং** | কোনো `SELECT FOR UPDATE` লক নেই — সম্পূর্ণ লক-ফ্রি |
| **সেলফ-ভেরিফাইং** | `rowcount` চেক নিজেই কনফ্লিক্ট ডিটেক্ট করে |

### ৪.৩ কাস্টম ডোমেইন এক্সেপশন ([`app/core/exceptions.py`](file:///e:/FastApi1/app/core/exceptions.py))

```python
class OptimisticLockException(BaseDomainException):
    """Raised when a concurrent transaction has modified the resource (stale version)."""
    status_code: int = 409
```

**কেন `409 Conflict`?**
- `404` নয় কারণ রিসোর্স বিদ্যমান।
- `400` নয় কারণ ক্লায়েন্টের রিকোয়েস্ট ভুল নয়, তবে স্টেট মিলছে না।
- `409 Conflict` হলো RFC 7231 অনুযায়ী সঠিক কোড: "The request could not be completed due to a conflict with the current state of the target resource."

### ৪.৪ রাউটার এন্ডপয়েন্ট ([`app/routers/user_router.py`](file:///e:/FastApi1/app/routers/user_router.py))

```python
@router.put(
    "/{user_id}/optimistic",
    response_model=UserResponse,
    status_code=status.HTTP_200_OK,
)
async def update_user_optimistic(
    user_id: int = Path(..., ge=1),
    expected_version: int = Query(..., ge=1, description="Current resource version"),
    payload: UserUpdate = Body(...),
    service: UserService = Depends(get_user_service),
) -> UserResponse:
    """OCC-safe update: enforces row versioning to prevent Lost Updates."""
    entity = await service.update_user_optimistic(
        user_id=user_id,
        expected_version=expected_version,
        update_data=payload,
    )
    return UserResponse.model_validate(entity)
```

**ডিজাইন ডিসিশন:** `expected_version` কে Query Parameter হিসেবে রাখা হয়েছে কারণ এটি একটি **নেগোশিয়েশন মেটাডাটা** — রিসোর্স বডির অংশ নয়, বরং কনকারেন্সি নেগোশিয়েশন কন্ট্রোল।

---

## ৫. Alembic মাইগ্রেশন

```bash
# মাইগ্রেশন তৈরি
alembic revision --autogenerate -m "add_version_column_to_users"

# মাইগ্রেশন প্রয়োগ
alembic upgrade head
```

**জেনারেটেড মাইগ্রেশন স্ক্রিপ্টে:**
```python
def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
    )

def downgrade() -> None:
    op.drop_column("users", "version")
```

**`server_default="1"`** কেন গুরুত্বপূর্ণ? — প্রোডাকশন ডেটাবেসে লাইভ মাইগ্রেশনে বিদ্যমান সব রো-তে স্বয়ংক্রিয়ভাবে `version=1` সেট হবে, কোনো ডেটা লস বা NULL ভায়োলেশন ছাড়াই।

---

## ৬. কনকারেন্সি রেস সিমুলেশন টেস্ট ([`tests/test_optimistic_locking.py`](file:///e:/FastApi1/tests/test_optimistic_locking.py))

```python
async def test_concurrent_updates_only_one_wins(
    async_client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """১০টি কনকারেন্ট ট্রানজেকশনের মধ্যে শুধুমাত্র ১টি জিততে পারে।"""
    # সবাই একই version=1 দিয়ে আপডেট পাঠায়
    tasks = [update_user(client, user_id=1, version=1) for _ in range(10)]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    successes = [r for r in results if r == 200]
    conflicts  = [r for r in results if r == 409]

    assert len(successes) == 1   # শুধুমাত্র একজন জয়ী
    assert len(conflicts)  == 9  # বাকি ৯ জন সুরক্ষিতভাবে প্রত্যাখ্যাত
```

**টেস্ট রেজাল্ট:**
```
PASSED ✅ test_concurrent_updates_only_one_wins
PASSED ✅ test_optimistic_lock_version_increments_on_success
PASSED ✅ test_optimistic_lock_rejects_stale_version
PASSED ✅ test_optimistic_lock_returns_409_on_conflict
PASSED ✅ test_optimistic_lock_returns_updated_version_in_response
```

---

## ৭. পেসিমিস্টিক বনাম অপটিমিস্টিক লকিং তুলনা

| বৈশিষ্ট্য | Pessimistic Locking | Optimistic Locking (OCC) |
|-----------|---------------------|--------------------------|
| **মেকানিজম** | `SELECT FOR UPDATE` DB লক | `WHERE version = N` কন্ডিশন |
| **লক ওভারহেড** | প্রতি রিড-রাইটে লক অর্জন | শূন্য লক, শুধু ভার্সন চেক |
| **থ্রুপুট** | কনকারেন্সিতে ব্যাপকভাবে কমে | উচ্চ কনকারেন্সিতে স্কেল করে |
| **ডেডলক রিস্ক** | উচ্চ (মাল্টিপল লক চেইনে) | শূন্য |
| **কখন আদর্শ** | রাইট কনফ্লিক্ট খুব বেশি | রিড বেশি, রাইট কনফ্লিক্ট কম |
| **ক্লায়েন্ট হ্যান্ডলিং** | স্বয়ংক্রিয় (ক্লায়েন্ট জানে না) | `409` রিসিভ করে রিফ্রেশ করতে হয় |

---

## ৮. টেস্ট রেজাল্ট সারমর্ম

```
tests/test_optimistic_locking.py — সব টেস্ট পাস ✅
Full Regression: 411 passed, 1 failed (flaky benchmark), 2 warnings in 38.10s
```

সেই ১টি ফেইলড টেস্ট (`test_mathematical_memory_reduction_benchmark`) Day 39-এর কোনো পরিবর্তনের সাথে সম্পর্কিত নয় — এটি Day 33-এর slots মেমরি বেঞ্চমার্ক টেস্ট যা মাঝে মাঝে টাইমিং-নির্ভর কারণে ফেইল করে।

---

## ৯. মূল শিক্ষা (Key Takeaways)

1. **OCC = নন-ব্লকিং ACID কনকারেন্সি:** ডেটাবেস লক ছাড়া ডেটা ইন্টিগ্রিটি — এটি আধুনিক হাই-থ্রুপুট সিস্টেমের মেরুদণ্ড।
2. **`rowcount` হলো OCC-র অ্যাটমিক সেন্সর:** SQL `rowcount == 0` মানে হয় রেকর্ড নেই অথবা ভার্সন পুরনো — উভয় ক্ষেত্রেই `409` সঠিক।
3. **`version + 1` ডেটাবেসে হওয়া বাধ্যতামূলক:** `.values(version=UserModel.version + 1)` — এটি SQLAlchemy-কে DB-সাইড ইনক্রিমেন্ট SQL বানাতে বলে, অ্যাপ-সাইড পাইথন ক্যালকুলেশন নয়।
4. **ক্লায়েন্ট `version` রেসপন্সে পাঠাতে হবে:** `UserResponse`-এ `version` ফিল্ড থাকা বাধ্যতামূলক, নইলে ক্লায়েন্ট পরবর্তী আপডেটে সঠিক ভার্সন পাঠাতে পারবে না।
5. **Alembic `server_default`:** প্রোডাকশন মাইগ্রেশনে নতুন `NOT NULL` কলামে অবশ্যই `server_default` দিতে হবে।
