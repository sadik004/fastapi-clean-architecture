# Day 42: এন্টারপ্রাইজ আইডেমপোটেন্সি কি আর্কিটেকচার ও ডাবল-চার্জিং প্রতিরোধ (Enterprise Idempotency Key Architecture & Double-Spending Prevention)

---

## ১. আমরা কী বানিয়েছি? (What did we build?)

আমরা আজ আন্তর্জাতিক ফিনটেক ও পেমেন্ট গেটওয়ে (Stripe এবং PayPal) মানের একটি পূর্ণাঙ্গ **আইডেমপোটেন্সি কি ইঞ্জিন (Enterprise Idempotency Key Engine)** তৈরি করেছি। 

গণিতে আইডেমপোটেন্সির সংজ্ঞা হলো $f(f(x)) = f(x)$ — অর্থাৎ একই অপারেশন একবার চালানো এবং দশবার চালানোর ফলাফল হুবহু এক হবে, কোনো বাড়তি পার্শ্বপ্রতিক্রিয়া (Side-effect) ঘটবে না।

আমাদের এই আর্কিটেকচারের মাধ্যমে:
1. প্রতিটি মিউটেটিং পেমেন্ট রিকোয়েস্টে ক্লায়েন্ট একটি ইউনিক হেডার পাঠায়: `Idempotency-Key` (যেমন UUID4 বা ক্লায়েন্ট টোকেন)।
2. রিকোয়েস্টের মেথড, URL পাথ এবং বডির ওপর $\mathcal{O}(L)$ টাইমে ক্রিপ্টোগ্রাফিক **SHA-256 হ্যাশ ফিঙ্গারপ্রিন্ট** তৈরি করা হয়।
3. **স্টেট মেশিন ট্রানজিশন (Redis State Machine):**
   - `"IN_PROGRESS"`: কাজ বর্তমানে একটি প্রসেস দ্বারা প্রক্রিয়াধীন রয়েছে।
   - `"COMPLETED"`: পেমেন্ট সফলভাবে সম্পন্ন হয়েছে এবং তার রেসপন্স ক্যাশে সংরক্ষিত।
   - `"FAILED"`: কোনো কারণে পেমেন্ট ফেইল করলে পুনরায় চেষ্টার অনুমতি প্রদান।
4. **পেলোড টেম্পারিং প্রতিরোধ (Payload Tampering Guard):** একই `Idempotency-Key` ব্যবহার করে কেউ যদি ভিন্ন টাকার অংক বা ভিন্ন অর্ডার পাঠাতে চায়, তবে সিস্টেম তাৎক্ষণিক `HTTP 422 Unprocessable Entity` দিয়ে রিকোয়েস্ট প্রত্যাখ্যান করে।
5. **ইন-ফ্লাইট কনকারেন্ট কলিশন লক (In-Flight Concurrency Protection):** একটি রিকোয়েস্ট চলার সময়েই যদি নেটওয়ার্কের ধীরগতির কারণে ক্লায়েন্ট বা ব্রাউজার একই কি দিয়ে আরেকটি রিকোয়েস্ট পাঠায়, তবে দ্বিতীয় রিকোয়েস্টটি সাথে সাথে `HTTP 409 Conflict` পেয়ে ফিরে যায়।
6. **জিরো ডুপ্লিকেট পেমেন্ট ও সাব-মিলিসেকেন্ড ক্যাশ রিপ্লে:** আগের সফল পেমেন্টের ক্ষেত্রে কোনো পেমেন্ট গেটওয়ে বা ডাটাবেস স্পর্শ না করেই Redis থেকে $\mathcal{O}(1)$ টাইমে হুবহু আগের রেসপন্স এবং `X-Cache-Lookup: HIT-IDEMPOTENT` হেডার ফিরিয়ে দেওয়া হয়।

---

## 📌 ব্যবহৃত DSA-এর সুনির্দিষ্ট নাম:
**আইডেমপোটেন্সি কি প্যাটার্ন ও ক্রিপ্টোগ্রাফিক রিকোয়েস্ট হ্যাশিং (Idempotency Key Pattern with Request Body SHA-256 Hashing & In-Flight Concurrency Locks) — O(L) Body Hash, O(1) Cache Replay**

## 🚀 প্রোডাকশনে ঠিক কখন ব্যবহার করব? (When to use in Production)
- **Stripe, bKash ও SSLCommerz পেমেন্ট গেটওয়ে:** ইউজার ডাবল ক্লিক করলে বা গেটওয়ের ওয়েবহুক (Webhook / IPN) নেটওয়ার্ক ড্রপের কারণে একই পেমেন্ট কনফার্মেশন ৩ বার পাঠালেও যেন একাউন্টে ৩ বার টাকা যোগ না হয়।
- **ই-কমার্স চেকআউট ও কার্ট অর্ডার প্লেসমেন্ট:** একই কার্ট যেন দুইবার অর্ডার হয়ে ইউজারের ডাবল টাকা না কাটে এবং ডাবল স্টক খালি না করে।
- **ব্যাংক ট্রান্সফার ও পে-আউট:** সেন্ড মানি বা ব্যাংক ট্রান্সফারের সময় নেটওয়ার্ক টাইমআউটের কারণে ক্লায়েন্ট রিট্রাই পাঠালেও ডাবল চার্জিং রোধ করা।

## 🎯 কী কারণে বা কোন পরিস্থিতিতে ব্যবহার করব? (Why to use / Technical Triggers)
- **নেটওয়ার্কের চরম অনিশ্চয়তায় ডাবল স্পেন্ডিং (Double-Spending) চিরতরে বন্ধ:** ক্লায়েন্ট টাইমআউট পেয়ে রিট্রাই পাঠালেও আইডেমপোটেন্ট গ্যারান্টি ($f(f(x)) = f(x)$) নিশ্চিত করে আগের সফল রেজাল্টই ফিরিয়ে দেওয়া।
- **পেলোড টেম্পারিং প্রতিরোধ (SHA-256 Guard):** একই আইডেমপোটেন্সি কি দিয়ে যদি কেউ টাকার পরিমাণ বা অর্ডার আইডি বদলে ফেলে, তবে সাথে সাথে HTTP 422 দিয়ে প্রত্যাখ্যান করা।
- **ইন-ফ্লাইট কনকারেন্ট প্রসেসিং লক:** ১ম রিকোয়েস্ট প্রসেসিং চলাকালীন (`IN_PROGRESS`) ২য় রিকোয়েস্ট এলে তাকে সাথে সাথে HTTP 409 দিয়ে সাময়িকভাবে আটকে দেওয়া।

---

## ২. বাস্তব জীবনের গল্প ও উপমা: কফি শপের কার্ড সোয়াইপ ও ডাবল পেমেন্টের ভীতি

কল্পনা করুন আপনি ধানমন্ডির একটি জনপ্রিয় ক্যাফেতে গিয়ে এক কাপ স্পেশাল ক্যাপুচিনো অর্ডার করলেন:
- বিল হলো ৩০০ টাকা। আপনি সেলস কাউন্টারে আপনার ডেবিট কার্ড সোয়াইপ মেশিনে দিলেন।
- কার্ড পাঞ্চ করার পর মেশিনের স্ক্রিনে গোল চাকা ঘুরছে ("Processing..."), কিন্তু দোকানের ওয়াইফাই হঠাৎ দুর্বল হয়ে সংযোগ বিচ্ছিন্ন হয়ে গেল!
- পিওএস (POS) মেশিন কোনো রিসিট বা কনফার্মেশন দিল না। সেলসম্যান বলল, "স্যার, নেটওয়ার্ক ফেইল করেছে, কার্ডটা আরেকবার দিন, আবার সোয়াইপ করি।"

### ❌ আইডেমপোটেন্সি কি ছাড়া কী বিপর্যয় ঘটত?
আপনি আবার কার্ড সোয়াইপ করলেন। কিন্তু সেলসম্যান জানত না যে প্রথম সোয়াইপের রিকোয়েস্টটি ব্যাংকের গেটওয়েতে পৌঁছে গিয়েছিল!
মিনিটখানেক পর আপনার মোবাইলে টুং করে পরপর দুটি এসএমএস এল:
- "আপনার অ্যাকাউন্ট থেকে ৩০০ টাকা কাটা হয়েছে।"
- "আপনার অ্যাকাউন্ট থেকে আরও ৩০০ টাকা কাটা হয়েছে!"
এক কাপ কফির জন্য আপনার ৬০০ টাকা কাটা গেল! আপনি রেগে আগুন হয়ে ক্যাফে ম্যানেজারের সাথে তুলকালাম শুরু করলেন, রিফান্ডের জন্য ফর্ম ফিলাপ করতে হলো এবং ১৫ দিন ব্যাংকের পেছনে ঘুরতে হলো।

### ✅ আইডেমপোটেন্সি কি ইঞ্জিন দিয়ে কী ঘটে?
আধুনিক ব্যাংকিং সিস্টেমে প্রথমবার সোয়াইপ করার মুহূর্তেই POS মেশিন একটি ইউনিক রসিদ টোকেন তৈরি করে পাঠায়: `Idempotency-Key: TXN-DHAKA-987654`:
1. সেন্ট্রাল ব্যাংকিং সার্ভার এই কি দিয়ে একটি স্টেট লক করে: `"IN_PROGRESS"`।
2. টাকা সফলভাবে কেটে সার্ভার রেকর্ড করে নেয়: `"COMPLETED"`, বিল: ৩০০ টাকা।
3. নেটওয়ার্ক ড্রপ করায় রিসিট না পেয়ে সেলসম্যান যখন ২য় বার একই বিলের জন্য কার্ড সোয়াইপ করে, POS মেশিন হুবহু একই `Idempotency-Key` আবার পাঠায়।
4. সার্ভার দেখে এই কি-এর পেমেন্ট তো মাত্র ৩০ সেকেন্ড আগেই সফল হয়েছে! 
5. সার্ভার কোনো দ্বিতীয় চার্জ না কেটে সাথে সাথে আগের ট্রানজ্যাকশন আইডি ও রিসিট স্ক্রিনে পাঠিয়ে দেয়: `X-Cache-Lookup: HIT-IDEMPOTENT`!
গ্রাহকের অ্যাকাউন্ট থেকে এক পয়সাও বাড়তি কাটা গেল না, অথচ সাথে সাথে প্রিন্টার দিয়ে রিসিট বের হয়ে এলো!

---

## ৩. এটা না বানালে কী মহাবিপদ হতো? (What Disaster Would Happen Without This?)

1. **ডাবল স্পেন্ডিং ও গ্রাহকের অর্থহানি:** একই অর্ডারের জন্য গ্রাহকের ব্যাংক একাউন্ট থেকে দুই বা ততোধিকবার টাকা কেটে নেওয়ার ফলে কোম্পানির বিরুদ্ধে আইনি মামলা ও জরিমানা হতো।
2. **মার্কেটপ্লেসের রিফান্ড জটিলতা:** ডাবল পেমেন্ট রিফান্ড করতে গিয়ে ক্রেডিট কার্ড গেটওয়ের অতিরিক্ত ২–৩% ট্রানজ্যাকশন ফি কোম্পানিকে নিজের পকেট থেকে ভর্তুকি দিতে হতো।
3. **পেলোড ফ্রড ও সিকিউরিটি ব্রিচ:** বৈধ ইউজারের টোকেন দিয়ে অন্য ভ্যালুর কার্ট চেকআউট করে পণ্যের দাম ও পরিমাণের গরমিল ঘটানো যেত।
4. **ডাটাবেস লক কনটেনশন ও থ্রুপুট ড্রপ:** প্রতিটি রিট্রাই রিকোয়েস্টে বারবার ডাটাবেস ও থার্ড-পার্টি গেটওয়েতে হিট করে পুরো সার্ভার স্লোডাউন হয়ে যেত।

---

## ৪. আমাদের প্রজেক্টের আসল কোড ও লাইন-বাই-লাইন সহজ ব্যাখ্যা

### ৪.১ কোর আইডেমপোটেন্সি ইঞ্জিন (`app/core/idempotency.py`)
```python
def compute_request_hash(method: str, path: str, body: bytes | str) -> str:
    raw_body = body.decode("utf-8", errors="replace") if isinstance(body, bytes) else str(body)
    # মেথড, পাথ এবং রিকোয়েস্ট বডি মিলিয়ে ইউনিক ফিঙ্গারপ্রিন্ট
    fingerprint = f"{method.upper()}:{path}:{raw_body}"
    return hashlib.sha256(fingerprint.encode("utf-8")).hexdigest()

class IdempotencyManager:
    async def check_or_acquire(
        self,
        key: str,
        request_hash: str,
        ttl_seconds: int = 86400,
    ) -> tuple[bool, dict[str, Any] | None]:
        redis_key = f"idempotency:{key}"
        initial_payload = {
            "state": IdempotencyState.IN_PROGRESS,
            "request_hash": request_hash,
            "status_code": None,
            "response_body": None,
            "created_at": datetime.now(UTC).isoformat(),
        }

        # ১. অ্যাটমিক SET NX EX: কি না থাকলেই কেবল IN_PROGRESS হিসেবে লক অধিকার
        set_success = await self._redis.set(
            redis_key,
            json.dumps(initial_payload),
            nx=True,
            ex=ttl_seconds,
        )

        if set_success:
            return False, None  # নতুন রিকোয়েস্ট, লক পেয়েছি

        # ২. কি আগে থেকেই আছে -> ডাটা রিড ও ভ্যালিডেশন
        raw_record = await self._redis.get(redis_key)
        record = json.loads(raw_record)
        stored_hash = record.get("request_hash")

        # ৩. পেলোড টেম্পারিং চেক
        if stored_hash and stored_hash != request_hash:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Idempotency key reused with different payload parameters.",
            )

        # ৪. কনকারেন্ট ইন-ফ্লাইট চেক
        if record.get("state") == IdempotencyState.IN_PROGRESS:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="A request with this idempotency key is currently in progress. Please wait.",
            )

        # ৫. পূর্বে সম্পন্ন হওয়া ক্যাশড রিপ্লে
        if record.get("state") == IdempotencyState.COMPLETED:
            return True, record

        return False, None
```

### ৪.২ পেমেন্ট রাউটার ইন্টিগ্রেশন (`app/routers/payment_router.py`)
```python
@router.post("/charge", status_code=status.HTTP_201_CREATED)
async def process_payment_charge_endpoint(
    request: Request,
    payload: PaymentChargeRequest,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=64)],
    payment_service: Annotated[PaymentService, Depends(get_payment_service)],
    idempotency_manager: Annotated[IdempotencyManager, Depends(get_idempotency_manager)],
) -> Response:
    raw_body = await request.body()
    request_hash = compute_request_hash(request.method, request.url.path, raw_body)

    # আইডেমপোটেন্সি চেক
    is_cached, cached_record = await idempotency_manager.check_or_acquire(
        key=idempotency_key,
        request_hash=request_hash,
    )

    # ক্যাশ হিট হলে তাৎক্ষণিক রিপ্লে
    if is_cached and cached_record:
        return JSONResponse(
            status_code=cached_record.get("status_code", 201),
            content=cached_record.get("response_body"),
            headers={"X-Cache-Lookup": "HIT-IDEMPOTENT"},
        )

    # আসল পেমেন্ট প্রসেসিং
    charge = await payment_service.process_charge(
        order_id=payload.order_id,
        amount=payload.amount,
        currency=payload.currency,
    )
    response_data = charge.model_dump(mode="json")

    # সফল স্টেট সংরক্ষণ
    await idempotency_manager.record_success(
        key=idempotency_key,
        request_hash=request_hash,
        status_code=201,
        response_body=response_data,
    )
    return JSONResponse(
        status_code=201,
        content=response_data,
        headers={"X-Cache-Lookup": "MISS"},
    )
```

---

## ৫. ডিএসএ ও স্টেট মেশিন মেকানিক্স (DSA & State Machine Lifecycle)

```
                       [Incoming Request with Idempotency-Key]
                                         │
                         Compute SHA-256 Request Hash: O(L)
                                         │
                    Redis SET idempotency:{key} ... NX EX 86400
                                   /           \
                 [Key did NOT exist]           [Key ALREADY exists]
                         │                               │
                Acquired IN_PROGRESS             Stored Hash == New Hash?
                         │                               /       \
                Run Payment Logic                      [NO]      [YES]
                         │                              │          │
                 Save to Redis as                   HTTP 422    State == IN_PROGRESS?
                    COMPLETED                      (Tampered)    /        \
                         │                                    [YES]       [NO]
                 Return 201 (MISS)                              │           │
                                                             HTTP 409    State == COMPLETED?
                                                           (In-Flight)      │
                                                                       Return Cached 201
                                                                       (HIT-IDEMPOTENT)
```

1. **হ্যাশিং কমপ্লেক্সিটি (Hashing Complexity):** $\mathcal{O}(L)$ time যেখানে $L$ হলো রিকোয়েস্ট বডির বাইট সংখ্যা।
2. **লুকআপ ও ট্রানজিশন কমপ্লেক্সিটি (State Transition Complexity):** $\mathcal{O}(1)$ time — Redis ইন-মেমরি হ্যাশ টেবিলে সাব-মিলিসেকেন্ড অ্যাক্সেস।
3. **মেমরি বাউন্ডেডনেস (Memory Boundedness):** প্রতিটি আইডেমপোটেন্সি রেকর্ডের জন্য বাধ্যতামূলক ২৪ ঘণ্টার টিটিএল (`EX 86400`) প্রযোজ্য, ফলে স্টোরেজ কখনো আনবাউন্ডেড গ্রো করে র‍্যাম ক্র্যাশ করায় না।

---

## ৬. ইন্টারভিউ ও ভাইভা প্রশ্ন (Interview Questions & Technical Deep Dive)

### প্রশ্ন ১: ডিস্ট্রিবিউটেড লক (Distributed Lock) এবং আইডেমপোটেন্সি কি (Idempotency Key) এর মধ্যে মূল পার্থক্য কী?
**উত্তর:**
- **কাজের পরিধি ও উদ্দেশ্য:** ডিস্ট্রিবিউটেড লক হলো একটি ক্ষণস্থায়ী সিঙ্ক্রোনাইজেশন টুল যা একই রিসোর্সে একাধিক প্রসেসের সমসাময়িক কাজ আটকে রাখে (Mutual Exclusion)। লক রিলিজ হয়ে গেলে অন্য কর্মী আবার কাজ করতে পারে।
- **ফলাফল সংরক্ষণ ও রিপ্লে:** আইডেমপোটেন্সি কি নিশ্চিত করে যে কাজটি সারাজীবনে (বা নির্দিষ্ট সময়সীমায়) মাত্র একবারই ঘটবে। প্রথমবার কাজ সম্পন্ন হওয়ার পর পরবর্তী প্রতিটি রিট্রাই রিকোয়েস্টে কাজ না চালিয়ে আগের ফলাফলটি ক্যাশ থেকে ফিরিয়ে দেওয়া হয়।
- **ব্যবহার:** লক ব্যবহার হয় কনকারেন্সি নিয়ন্ত্রণে; আইডেমপোটেন্সি ব্যবহার হয় নেটওয়ার্ক রিট্রাই ও ডাবল-চার্জিং রোধে।

### প্রশ্ন ২: আইডেমপোটেন্সি কি সিস্টেমে SHA-256 রিকোয়েস্ট হ্যাশ যাচাই করা কেন বাধ্যতামূলক?
**উত্তর:**
যদি শুধুমাত্র `Idempotency-Key` দিয়ে চেক করা হতো এবং রিকোয়েস্টের বডি মেলানো না হতো, তবে মারাত্মক নিরাপত্তা বিপর্যয় ঘটত:
একজন গ্রাহক একটি অর্ডারে ১০০ টাকার পেমেন্ট করার পর তার ব্রাউজার বা কোনো আক্রমণকারী একই কি ব্যবহার করে ৫০০ টাকার ভিন্ন অর্ডার পাঠালে সার্ভার ভাবত এটা আগের রিকোয়েস্টের রিট্রাই, এবং ৫০০ টাকার অর্ডারের পণ্য দিয়ে দিত অথচ গ্রাহকের কাছ থেকে আর কোনো টাকাই কাটত না!
SHA-256 হ্যাশ গার্ড নিশ্চিত করে যে ক্লায়েন্ট ঠিক যে প্যারামিটারগুলো দিয়ে প্রথমবার রিকোয়েস্ট পাঠিয়েছিল, হুবহু সেই প্যারামিটার এলেই কেবল ক্যাশড ফলাফল রিপ্লে করা হবে; প্যারামিটারে ১টি অক্ষরের পরিবর্তন হলেও সাথে সাথে `HTTP 422` দেওয়া হবে।

---

## ৭. এক নজরে আসল মূল লজিক (Summary in 3 Lines)
1. **`SET NX EX` দিয়ে ইন-ফ্লাইট মিউটেক্স:** প্রথম রিকোয়েস্ট `IN_PROGRESS` লক পায়, কনকারেন্ট রিট্রাই পায় ৪0৯ কনফ্লিক্ট।
2. **SHA-256 দিয়ে পেলোড টেম্পারিং প্রতিরোধ:** কি একই রেখে ডাটা পরিবর্তন করলেই সাথে সাথে ৪২২ আনপ্রসেসেবল এন্টিটি এরর।
3. **ক্যাশড রিপ্লেতে ডাবল-চার্জিং রোধ:** সফল ট্রানজ্যাকশন `COMPLETED` অবস্থায় ক্যাশ থাকে, পরবর্তী রিট্রাই সাব-মিলিসেকেন্ডে আগের রেসপন্স পেয়ে যায়।
