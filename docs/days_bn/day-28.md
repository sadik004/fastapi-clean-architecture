# Day 28: অ্যাডভান্সড অ্যাসিনক্রোনাস টেস্টিং (pytest-asyncio, AsyncMock ও ডিপেন্ডেন্সি ওভাররাইড)

---

### 📌 ব্যবহৃত DSA ও আর্কিটেকচারাল প্যাটার্নের নাম (Exact Data Structure & Architectural Pattern)
- **Async Test Double Pattern, Dynamic Dependency Inversion Overrides & In-Memory Coroutine Spying ($\mathcal{O}(1)$ Space-Time Overhead, Zero Network/Disk I/O)**: অ্যাসিনক্রোনাস টেস্ট ডাবল (AsyncMock), ফাস্টএপিআই ডিপেন্ডেন্সি ইনভার্সন ওভাররাইড এবং মেমোরিতে পিওর করুটিন স্পাইং আর্কিটেকচার।

### 🚀 প্রোডাকশনে ঠিক কখন ব্যবহার করব? (When to use in Production)
- **পেমেন্ট গেটওয়ে ও এক্সটার্নাল থার্ড-পার্টি API টেস্টিং**: Stripe, bKash, বা Twilio-এর মতো পেইড বা রেট-লিমিটেড এক্সটার্নাল এপিআই-তে আসল রিকোয়েস্ট না পাঠিয়ে শতভাগ আইসোলেশনে এপিআই ইন্টিগ্রেশন ও ফেলওভার লজিক টেস্ট করা।
- **ডাটাবেস ও ডিস্ক ক্র্যাশ রেজিলিয়েন্স ভেরিফিকেশন (Chaos & Failure Injection)**: প্রোডাকশন ডাটাবেসে কানেকশন ড্রপ, ডিস্ক স্পেস ফুল (`RuntimeError`), অথবা নেটওয়ার্ক টাইমআউট হলে ব্যাকএন্ড সার্ভিস কীভাবে আচরণ করে এবং নিরাপদ এরর মেসেজ দেয় কিনা তা যাচাই করা।
- **হাই-স্পিড CI/CD পাইপলাইন এক্সিকিউশন**: প্রতিবার গিট পুশ বা পিআর (PR) চেকআউটে শত শত ইউনিট টেস্ট কয়েক সেকেন্ডের মধ্যে (< ১-২ সেকেন্ড) সম্পন্ন করতে ইন-মেমরি মকিং ব্যবহার করা, যাতে কোনো ক্লাউড ডাটাবেস স্পিন-আপ করতে না হয়।
- **ব্যাকগ্রাউন্ড জব ও নোটিফিকেশন স্পাইং**: ইমেইল বা এসএমএস সেন্ডার ব্যাকগ্রাউন্ড টাস্কে যোগ হয়েছে কিনা এবং সঠিক প্যারামিটার সহ কল হয়েছে কিনা তা কোনো বিলম্ব ছাড়াই `assert_awaited_once_with` দিয়ে পরীক্ষা করা।

### 🎯 কী কারণে বা কোন পরিস্থিতিতে ব্যবহার করব? (Why to use / Technical Triggers)
- **সিঙ্ক্রোনাস `MagicMock` দিয়ে অ্যাসিনক্রোনাস মেথড মক করার মারাত্মক ভুল দূর করা**: সাধারণ `MagicMock` দিয়ে `async def` মেথড মক করলে পাইথনে করুটিন তৈরি হয় না বা `RuntimeWarning: coroutine was never awaited` وار্নিং দিয়ে পুরো টেস্ট স্যুট ফেইল করে; `AsyncMock` আসল অ্যাসিঙ্ক করুটিন প্রোটোকল মেনে চলে।
- **আসল ক্রেডিট কার্ড ও ব্যাংক একাউন্টে অনাকাঙ্ক্ষিত চার্জ প্রতিরোধ**: টেস্ট চলাকালে ভুলেও যেন লাইভ পেমেন্ট গেটওয়ে বা লাইভ এসএমএস গেটওয়েতে রিকোয়েস্ট না যায় এবং কোম্পানির ক্রেডিট ব্যালেন্স শেষ না হয়।
- **ফাস্টএপিআই ডিপেন্ডেন্সি ট্রি-তে রুট লেয়ার আইসোলেশন**: ডাটাবেস সেশন বা রিপোজিটরি তৈরি না করেই `app.dependency_overrides` দিয়ে সরাসরি মেমোরি মক ইনজেক্ট করে এন্ডপয়েন্টের রিকোয়েস্ট ভ্যালিডেশন ও স্ট্যাটাস কোড দ্রুত ভেরিফাই করা।
- **টেস্ট পলিউশন ও স্টেট লিকেজ প্রতিরোধ**: টেস্ট শেষ হওয়ার সাথে সাথে `dependency_overrides.pop()` বা `clear()` নিশ্চিত করে পরবর্তী টেস্টের জন্য পরিবেশকে ১০০% সতেজ রাখা।

---

## ১. আমরা কী বানিয়েছি? (What did we build?)

আজকে আমরা আমাদের আর্কিটেকচারে সম্পূর্ণ প্রোডাকশন-গ্রেড অ্যাসিনক্রোনাস টেস্ট আইসোলেশন সিস্টেম তৈরি করেছি (`tests/conftest.py` এবং `tests/test_async_mocking.py`):

1. **সেন্ট্রালাইজড অ্যাসিঙ্ক মক ফিক্সচার (`tests/conftest.py`)**:
   - `mock_user_repository`: একটি উচ্চ-ক্ষমতাসম্পন্ন `AsyncMock(spec=UserRepositoryProtocol)` যা আসল রিপোজিটরির সমস্ত মেথড (`create`, `get_by_id`, `get_by_email`, `get_by_username`, `delete`, `update`, `list_all`) সিমুলেট করে।
   - `mock_notification_service`: একটি অ্যাসিঙ্ক স্পাই (`MockNotificationService`) যা ব্যাকগ্রাউন্ডে পাঠানো `send_welcome_notification` এবং `record_audit_log` পর্যবেক্ষণ করে।
   - `mock_db_session`: SQLAlchemy-এর `AsyncSession`-এর একটি ডাবল যা `execute`, `commit`, `rollback` মেথডগুলোকে মক করে।
2. **আইসোলেটেড সার্ভিস ইউনিট টেস্ট (`test_user_service_register_user_success`)**:
   - কোনো ডাটাবেস বা নেটওয়ার্ক ছাড়াই `UserService`-এর লজিক, পাসওয়ার্ড হ্যাশিং এবং প্রিফিক্স ট্রাই ইনডেক্সিং যাচাই।
3. **ফেলিউর ইনজেকশন ও ক্যাওস টেস্টিং (`test_user_service_database_failure_injection`)**:
   - ডাটাবেস ফেইল করলে (`mock.create.side_effect = RuntimeError(...)`) সার্ভিস লেয়ার নিরাপদে এক্সেপশন প্রোপাগেট করে কিনা তা প্রমাণ করা।
4. **গেটওয়ে রেজিলিয়েন্স ও ব্যাকগ্রাউন্ড টাস্ক আইসোলেশন (`test_route_resilience_background_task_smtp_timeout_isolation`)**:
   - এসএমটিপি সার্ভার টাইমআউট (`TimeoutError`) হলেও এপিআই রেসপন্স যেন সফল থাকে (HTTP 201 Created) এবং ক্লায়েন্ট রেসপন্স ক্ষতিগ্রস্ত না হয় তা নিশ্চিত করা।
5. **ফাস্টএপিআই ডিপেন্ডেন্সি ওভাররাইড রাউট টেস্ট (`test_route_dependency_override_create_user`)**:
   - `app.dependency_overrides[get_user_repository] = lambda: mock_user_repository` ব্যবহার করে সাব-মিলিসেকেন্ডে এন্ডপয়েন্ট টেস্ট করা।
6. **সাব-৫০ মিলিসেকেন্ড পারফরম্যান্স বেঞ্চমার্ক (`test_mock_batch_execution_sub_50ms_benchmark`)**:
   - ১০টি মকড অ্যাসিঙ্ক অপারেশনের ব্যাচ ৫০ মিলিসেকেন্ডের অনেক কম সময়ে (< ২০ms) সম্পন্ন করে জিরো-আই/ও গ্যারান্টি দেওয়া।

---

## ২. বাস্তব জীবনের গল্প ও উপমা (Real-World Analogy)

### পাইলটের ফ্লাইট সিমুলেটর ও সিনেমার স্টান্টম্যানের ডামি (The Flight Simulator & Stunt Dummy Analogy)

কল্পনা করুন দুটি বাস্তব দৃশ্য:
- **১. বিমান চালকের প্রশিক্ষণ (Flight Simulator Analogy)**:
  - কোনো নতুন পাইলটকে কি প্রশিক্ষণ দেওয়ার জন্য প্রথম দিনই আকাশে ৩০০ যাত্রীসহ আসল বোয়িং ৭৭৭ বিমানে তুলে দেওয়া হবে এবং ইঞ্জিন বন্ধ করে দিয়ে পরীক্ষা করা হবে সে বিমান সামলাতে পারে কিনা? কখনোই না! আসল বিমান নিয়ে পরীক্ষা করলে বিমান ক্র্যাশ করবে এবং কোটি টাকার ক্ষতি হবে।
  - এর বদলে পাইলটকে বসানো হয় একটি **ফ্লাইট সিমুলেটরে (AsyncMock)**। সেখানে কম্পিউটার স্ক্রিন ও হাইড্রোলিক কেবিনে ঠিক আসল বিমানের মতো বাটন, লিভার ও আকাশ তৈরি করা হয়। প্রশিক্ষক ইচ্ছা করে সিমুলেটরে ইঞ্জিনের আগুন বা ঝড় তৈরি করেন (`side_effect = RuntimeError("Engine failure")`)। পাইলট নিরাপদে সিমুলেটরে পরীক্ষা দেয়।
- **২. সিনেমার শুটিংয়ে ডামি (Stunt Dummy / Dependency Override Analogy)**:
  - একটি অ্যাকশন সিনেমায় নায়ককে দশ তলা বিল্ডিং থেকে লাফ দিতে হবে। আসল নায়ক যদি লাফ দেন, তবে তিনি আহত হবেন এবং শুটিং বন্ধ হয়ে যাবে।
  - তাই পরিচালকরা আসল নায়কের জায়গায় একটি **স্টান্ট ডামি পুতুল (Dependency Override)** ঝুলিয়ে দেন। ক্যামেরা দূর থেকে দেখে মনে করে আসল নায়কই কাজ করছেন, কিন্তু নায়কের কোনো ক্ষতি হয় না!
  - আমাদের ব্যাকএন্ড সিস্টেমে আসল ডাটাবেস বা ব্যাংক একাউন্ট হলো আসল নায়ক, আর `AsyncMock` হলো সেই নিরাপদ ডামি পুতুল!

---

## ৩. এটা না বানালে কী মহাবিপদ হতো? (The Production Disaster Without It)

যদি এন্টারপ্রাইজ প্রজেক্টে প্রোপার অ্যাসিঙ্ক মকিং না থাকত, তবে নিচের মারাত্মক বিপর্যয়গুলো ঘটত:

1. **আসল ক্রেডিট কার্ডে চার্জ ও টেস্ট এসএমএস স্প্যাম (Financial & Brand Disaster)**:
   - ডেভেলপাররা লোকাল মেশিনে টেস্ট রান করার সময় যদি আসল Stripe বা Twilio API হিট হতো, তবে কোম্পানির আসল ব্যাংক কার্ড থেকে হাজার হাজার ডলার ফি কেটে যেত অথবা হাজার হাজার আসল কাস্টমারের মোবাইলে "Test OTP" মেসেজ চলে গিয়ে ব্র্যান্ডের সুনাম নষ্ট হতো।
2. **ধীরগতির CI/CD পাইপলাইন ও প্রোডাকশন ব্লকার (The 45-Minute CI Nightmare)**:
   - প্রতিটি টেস্টে আসল ডাটাবেসে কানেক্ট ও ডিস্ক রাইট করতে গেলে একেকটি টেস্ট নিতে পারত ১০০–২০০ms। ১০০০টি টেস্ট রান করতে ৩০ থেকে ৪৫ মিনিট লেগে যেত! ডেভেলপাররা পিআর মার্চ করার জন্য ঘণ্টার পর ঘণ্টা অপেক্ষায় বসে থাকত। পক্ষান্তরে, মক টেস্টে ১০০০টি টেস্ট মাত্র ৩–৪ সেকেন্ডে শেষ হয়।
3. **ফ্ল্যাকি টেস্ট ও ফলস অ্যালার্ম (Flaky Network Tests)**:
   - টেস্ট চলার সময় যদি বাইরের কোনো থার্ড পার্টি সার্ভার ডাউন থাকত বা ইন্টারনেট স্পিড কমে যেত, তবে আমাদের কোডে কোনো বাগ না থাকা সত্ত্বেও CI বিল্ড লাল (Failed) হয়ে যেত।
4. **`coroutine was never awaited` ক্র্যাশ ও আনহ্যান্ডেল্ড লিক**:
   - সাধারণ `MagicMock` ব্যবহার করলে পাইথন অ্যাসিঙ্ক লুপ মনে করত মেথডটি নন-অ্যাসিঙ্ক। এর ফলে কোডে `await` করার চেষ্টা করলে টাইপ এরর হতো এবং করুটিনগুলো ইভেন্ট লুপে আন-অ্যাওয়েটেড থেকে মেমোরি লিক ঘটাত।

---

## ৪. আমাদের প্রজেক্টের আসল কোড ও লাইনের সহজ ব্যাখ্যা (Real Code & Detailed Line-by-Line Breakdown)

### ক. সেন্ট্রালাইজড অ্যাসিঙ্ক রিপোজিটরি মক (`tests/conftest.py`)
```python
@pytest.fixture
def mock_user_repository() -> AsyncMock:
    """UserRepositoryProtocol সন্তুষ্টকারী পুনঃব্যবহারযোগ্য AsyncMock."""
    mock = AsyncMock(spec=UserRepositoryProtocol)
    mock.get_by_id.return_value = None
    mock.get_by_email.return_value = None
    mock.get_by_username.return_value = None
    mock.create.return_value = UserEntity(
        id=1,
        email="mocked.user@example.com",
        username="mocked_user",
        password_hash="mocked_hash",
        is_active=True,
        created_at=datetime.now(timezone.utc),
        age=25,
        role="user",
        full_name="Mocked User",
    )
    mock.delete.return_value = True
    return mock
```
- **`AsyncMock(spec=UserRepositoryProtocol)`**: পাইথনের `spec` প্যারামিটার নিশ্চিত করে যে মক অবজেক্টে কেবল `UserRepositoryProtocol`-এ উল্লেখিত মেথডগুলোই থাকবে। কোনো ডেভেলপার ভুল নামে মেথড কল করলে টেস্ট সাথে সাথে `AttributeError` দিয়ে আটকে যাবে।
- **`mock.create.return_value = ...`**: মেথডটিকে কল করলে কোনো ডাটাবেস স্পর্শ না করেই তাৎক্ষণিক একটি মেমোরি-অপ্টিমাইজড `UserEntity` রিটার্ন করবে।

---

### খ. ফেলিউর ইনজেকশন ও ক্যাওস টেস্টিং (`tests/test_async_mocking.py`)
```python
@pytest.mark.asyncio
async def test_user_service_database_failure_injection(mock_user_repository: AsyncMock) -> None:
    # ডাটাবেস ক্র্যাশ বা ডিস্ক ফুল হওয়ার কৃত্রিম বিপর্যয় ইনজেক্ট করা
    mock_user_repository.create.side_effect = RuntimeError("Database disk full / connection timeout")

    service = UserService(repository=mock_user_repository, trie=PrefixTrie())
    payload = UserCreate(...)

    # নিশ্চিত করা যে সার্ভিস লেয়ার কোনো অভ্যন্তরীণ স্টেট নষ্ট না করে এক্সেপশন প্রোপাগেট করে
    with pytest.raises(RuntimeError, match="Database disk full / connection timeout"):
        await service.register_user(payload)

    # ভেরিফাই করা যে ক্রিয়েট মেথডটি ঠিক একবারই এক্সিকিউট করার চেষ্টা করা হয়েছিল
    mock_user_repository.create.assert_awaited_once()
```
- **`side_effect`**: সাধারণ রিটার্ন ভ্যালুর বদলে একটি এরর অবজেক্ট ইনজেক্ট করা হয়। যখনই কোড `await repo.create(...)` কল করে, তখনই পাইথন এই নির্দিষ্ট এররটি রেইজ করে। এভাবে আমরা ক্যাওস ইঞ্জিনিয়ারিং ও ফল্ট টলারেন্স পরীক্ষা করি।

---

### গ. ব্যাকগ্রাউন্ড নোটিফিকেশন স্পাইং ও ক্লায়েন্ট রেজিলিয়েন্স
```python
def test_route_resilience_background_task_smtp_timeout_isolation(
    mock_notification_service: MockNotificationService,
) -> None:
    # এসএমটিপি সার্ভার টাইমআউট হলেও ক্লায়েন্ট যেন 201 রেসপন্স পায়
    mock_notification_service.send_welcome_notification.side_effect = TimeoutError("SMTP timeout")

    client = TestClient(app, raise_server_exceptions=False)
    response = client.post("/users/", json=payload)

    assert response.status_code == 201
    assert "X-Request-ID" in response.headers
```
- **`raise_server_exceptions=False`**: প্রোডাকশনে যখন উভিকর্ন (Uvicorn) চলে, ব্যাকগ্রাউন্ড টাস্কে কোনো এরর হলে তা সার্ভার লগে প্রিন্ট হয় কিন্তু ক্লায়েন্টের কাছে ইতিমধ্যে চলে যাওয়া HTTP 201 রেসপন্স ক্ষতিগ্রস্ত হয় না। `raise_server_exceptions=False` টেস্ট ক্লায়েন্টকে ঠিক প্রোডাকশন সার্ভারের মতোই আচরণ করায়।

---

### ঘ. ডিপেন্ডেন্সি ওভাররাইড ও ক্লিন টিয়ারডাউন
```python
def test_route_dependency_override_create_user(
    client: TestClient,
    mock_user_repository: AsyncMock,
) -> None:
    # ফাস্টএপিআই-এর ডিপেন্ডেন্সি ট্রিতে আসল রিপোজিটরির বদলে মক বসানো
    app.dependency_overrides[get_user_repository] = lambda: mock_user_repository
    try:
        response = client.post("/users/", json=payload)
        assert response.status_code == 201
        mock_user_repository.create.assert_awaited_once()
    finally:
        # টেস্ট শেষ হওয়া মাত্রই ওভাররাইড মুছে দেওয়া যাতে পরবর্তী কোনো টেস্ট দূষিত না হয়
        app.dependency_overrides.pop(get_user_repository, None)
```
- **`app.dependency_overrides`**: ফাস্টএপিআই-এর সবচেয়ে শক্তিশালী ডিপেন্ডেন্সি ইনজেকশন ফিচার। এটি রানটাইমে যেকোনো `Depends()` প্রোভাইডারকে অন্য ফাংশন বা মক দিয়ে প্রতিস্থাপন করে।
- **`try...finally:` প্যাটার্ন**: টেস্ট পাস হোক বা ফেইল করুক, `finally:` ব্লকে `dependency_overrides.pop(...)` নিশ্চিত করে যে অন্য টেস্টগুলোর জন্য সিস্টেম ক্লিন থাকবে।

---

## ৫. ডিএসএ ও টেস্ট পারফরম্যান্স মেকানিক্স (Space-Time Budget)

| মেট্রিক / বৈশিষ্ট্য | বাস্তব ডাটাবেস ও নেটওয়ার্ক টেস্ট | AsyncMock ও ডিপেন্ডেন্সি ওভাররাইড | পারফরম্যান্স লাভ (Speedup) |
| :--- | :--- | :--- | :--- |
| **১টি টেস্টের এক্সিকিউশন টাইম** | ৫০ms – ২৫০ms | **০.০২ms – ০.৫ms** | **১০০ থেকে ৫০০ গুণ দ্রুত!** |
| **১০টি টেস্টের ব্যাচ রান** | ৫০০ms – ২,৫০০ms | **< ১৫ms** | **১০০ গুণ দ্রুত** |
| **ডিস্ক ও নেটওয়ার্ক I/O** | ফাইল রাইট, সকেট কানেকশন | **জিরো (Zero I/O)** | **সম্পূর্ণ মেমোরি ভিত্তিক** |
| **স্পেস জটিলতা** | ডাটাবেস টেবিল সাইজ | **$\mathcal{O}(1)$ মেমোরি পয়েন্টার** | **জিরো মেমোরি ব্লট** |
| **আইসোলেশন নিশ্চয়তা** | ডাটাবেসে টেবিল ক্লিনিং লাগে | **সম্পূর্ণ স্বাধীন** | **জিরো টেস্ট পলিউশন** |

---

## ৬. টেস্টিং স্ট্র্যাটেজি ও কোয়ালিটি গেট (Testing Strategy)

আমরা `tests/test_async_mocking.py`-তে ১০টি টেস্ট সম্পন্ন করেছি:
1. **সার্ভিস রেজিস্ট্রেশন সাকসেস (`test_user_service_register_user_success`)**: মক রিপোজিটরি ব্যবহার করে জিরো-আই/ও মেমোরি রেজিস্ট্রেশন।
2. **ডুপ্লিকেট ইমেইল রিজেকশন (`test_user_service_register_user_duplicate_email_rejection`)**: ইউনিকনেস লজিক ভেরিফিকেশন।
3. **ইউজার রিট্রিভাল ও ডিলিশন (`test_user_service_get_by_id_and_delete`)**: `get_by_id` এবং `delete` মেথডের অ্যাসিঙ্ক কল নিশ্চিত করা।
4. **ইউজার নট ফাউন্ড এরর (`test_user_service_get_by_id_not_found`)**: মক `None` রিটার্ন করলে ডোমেইন এক্সেপশন নিশ্চিত করা।
5. **ডাটাবেস ক্যাওস ইনজেকশন (`test_user_service_database_failure_injection`)**: ডিস্ক ক্র্যাশ সিমুলেশনে সার্ভিস রেজিলিয়েন্স।
6. **এসএমটিপি টাইমআউট আইসোলেশন (`test_route_resilience_background_task_smtp_timeout_isolation`)**: ব্যাকগ্রাউন্ড ফেইল করলেও ক্লায়েন্টের জন্য HTTP 201 অক্ষত থাকা।
7. **নোটিফিকেশন ও অডিট স্পাই ভেরিফিকেশন (`test_notification_service_spies_verified_on_user_creation`)**: আর্গুমেন্টসহ ব্যাকগ্রাউন্ড টাস্ক এক্সিকিউশন যাচাই।
8. **রাউট ডিপেন্ডেন্সি ওভাররাইড ক্রিয়েট (`test_route_dependency_override_create_user`)**: এন্ডপয়েন্টে মক ইনজেকশন।
9. **রাউট ডিপেন্ডেন্সি ওভাররাইড রিট্রিভ (`test_route_dependency_override_get_user_by_id`)**: পাথ প্যারামিটার সহ মক কল যাচাই।
10. **সাব-৫০ মিলিসেকেন্ড পারফরম্যান্স বেঞ্চমার্ক (`test_mock_batch_execution_sub_50ms_benchmark`)**: ১০টি অ্যাসিঙ্ক কল ৫০ মিলিসেকেন্ডের অনেক কম সময়ে শেষ হওয়া।

---

## ৭. ইন্টারভিউ ও ভাইভা প্রশ্ন (Interview Questions & Model Answers)

### প্রশ্ন ১: "অ্যাসিনক্রোনাস ফাংশন মক করার সময় `unittest.mock.MagicMock` ব্যবহার না করে কেন `AsyncMock` ব্যবহার করতে হবে?"
**মডেল উত্তর**:
> "`MagicMock` হলো একটি সিঙ্ক্রোনাস টেস্ট ডাবল। যখন কোনো কোড `await target()` কল করে, তখন অবজেক্টটিকে অবশ্যই একটি অ্যাসিঙ্ক করুটিন (Coroutine) রিটার্ন করতে হয়। `MagicMock` সাধারণ রিটার্ন ভ্যালু দেয় যা করুটিন নয়, ফলে রানটাইমে `TypeError: object MagicMock can't be used in 'await' expression` অথবা `RuntimeWarning: coroutine was never awaited` এরর ঘটে। পক্ষান্তরে, `AsyncMock` নিজেই একটি অরিজিনাল অ্যাসিঙ্ক করুটিন অবজেক্ট রিটার্ন করে এবং `assert_awaited_once_with()`, `await_count`-এর মতো অ্যাসিঙ্ক অ্যাসারশন মেথড সরবরাহ করে যা রিয়েল অ্যাসিঙ্ক লাইফসাইকেল নির্ভুলভাবে টেস্ট করে।"

### প্রশ্ন ২: "FastAPI-তে ইন্টিগ্রেশন টেস্ট লেখার সময় `app.dependency_overrides` ব্যবহারের সেরা উপায় কী এবং টিয়ারডাউন না করলে কী সমস্যা হয়?"
**মডেল উত্তর**:
> "`app.dependency_overrides` হলো একটি গ্লোবাল ডিকশনারি যেখানে কী (Key) হিসেবে অরিজিনাল ডিপেন্ডেন্সি ফাংশন এবং ভ্যালু (Value) হিসেবে মক প্রোভাইডার রাখা হয়। এটি ব্যবহারের নিয়ম হলো টেস্টের ভেতরে ওভাররাইড সেট করা এবং অবশ্যই `try...finally:` ব্লকে বা `pytest fixture` টিয়ারডাউনে `app.dependency_overrides.pop(original_func, None)` বা `.clear()` কল করা। যদি টিয়ারডাউন না করা হয়, তবে একটি টেস্টের তৈরি মক অন্য টেস্টগুলোতেও কার্যকর থেকে যাবে। একে 'Test State Pollution' বলা হয়, যা টেস্ট স্যুটে ফ্ল্যাকি টেস্ট বা অপ্রত্যাশিত ফেইলিউর তৈরি করে।"

---

## ৮. এক নজরে আসল মূল লজিক (The Junior Architect's Operational Summary)

1. **অ্যাসিঙ্ক কোডে শুধুই AsyncMock**: যেকোনো `async def` ফাংশন বা প্রোটোকল মেথড মক করতে সর্বদা `AsyncMock(spec=...)` ব্যবহার করতে হবে; ভুলেও সিঙ্ক্রোনাস `MagicMock` নয়।
2. **ডিপেন্ডেন্সি ওভাররাইডে গ্যারান্টিড টিয়ারডাউন**: `app.dependency_overrides` ব্যবহার করার পর সর্বদা `try...finally:` বা ফিক্সচার টিয়ারডাউনে রিমুভ নিশ্চিত করতে হবে।
3. **ক্যাওস ও রেজিলিয়েন্স টেস্টিং আবশ্যক**: প্রোডাকশনের কোনো এক্সটার্নাল এপিআই (পেমেন্ট, ইমেইল, ডাটাবেস) ১০০% নিরবচ্ছিন্ন নয়; তাই `side_effect` দিয়ে টাইমআউট ও ক্র্যাশ সিমুলেট করে ফল্ট টলারেন্স পরীক্ষা করতে হবে।
