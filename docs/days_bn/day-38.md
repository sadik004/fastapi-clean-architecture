# Day 38: টোকেন বাকেট রেট লিমিটিং ও রেডিস অ্যাটমিক লুয়া স্ক্রিপ্টিং (Token Bucket Rate Limiting Architecture with Atomic Redis Lua Scripts)

---

## ১. আমরা কী বানিয়েছি?

আমরা আজ ক্লাউড ইন্ডাস্ট্রির স্ট্যান্ডার্ড (AWS API Gateway, Stripe, GitHub-এ ব্যবহৃত) **টোকেন বাকেট অ্যালগরিদম (Token Bucket Algorithm)** তৈরি করেছি, যা চালিত হচ্ছে **রেডিস অ্যাটমিক লুয়া স্ক্রিপ্ট (Atomic Redis Lua Scripting via EVALSHA)** দিয়ে। এর মাধ্যমে:
1. ক্লায়েন্ট প্রতি মাত্র ২টি সংখ্যা (`tokens` ও `last_updated`) সংরক্ষণ করে $\mathcal{O}(1)$ আল্ট্রা-লাইটওয়েট মেমোরি খরচ (< ১০০ বাইট) নিশ্চিত করা হয়েছে।
2. রেডিসের একক এক্সিকিউশন থ্রেডের ভেতরে লুয়া স্ক্রিপ্ট চালিয়ে কনকারেন্ট রিকোয়েস্টের মাঝে রেস কন্ডিশন (Race Condition) ১০০% নির্মূল করা হয়েছে।
3. ক্লায়েন্টের স্বাভাবিক বাস্ট ট্রাফিককে (যেমন এক সেকেন্ডে ৫টি রিকোয়েস্ট) অনুমতি দিয়ে পরবর্তীতে একটি নির্দিষ্ট হারে (যেমন ১টি টোকেন/সেকেন্ড) ট্রাফিক শেপিং নিশ্চিত করা হয়েছে।

---

## 📌 ব্যবহৃত DSA-এর সুনির্দিষ্ট নাম:
**টোকেন বাকেট অ্যালগরিদম (Token Bucket Algorithm) ও রেডিস অ্যাটমিক লুয়া স্ক্রিপ্টিং (EVALSHA) — O(1) Time, O(1) Space Memory Efficiency**

## 🚀 প্রোডাকশনে ঠিক কখন ব্যবহার করব? (When to use in Production)
- **পাবলিক ক্লাউড এপিআই গেটওয়ে (যেমন AWS, Stripe, GitHub API):** যেখানে ক্লায়েন্ট বা ব্রাউজারের স্বাভাবিক ট্রাফিক বাস্ট (Burst) হ্যান্ডেল করতে হয়।
- **ই-কমার্স চেকআউট বা ফ্ল্যাশ সেল:** ইউজার পেজ লোডের সময় একসাথে ৪–৫টি এপিআই কল করলেও যেন তা সুন্দরভাবে পাস হতে পারে।
- **আল্ট্রা-লাইটওয়েট রেট লিমিটিং:** যেখানে কোটি কোটি ইউজারের রেট লিমিট হিসাব রাখতে মেমোরি খরচ নূন্যতম রাখতে হয়।

## 🎯 কী কারণে বা কোন পরিস্থিতিতে ব্যবহার করব? (Why to use / Technical Triggers)
- **স্লাইডিং উইন্ডোর চেয়ে ৯০%+ মেমোরি সাশ্রয় (O(1) Space):** প্রতি ক্লায়েন্টের জন্য ZSET-এ শত শত টাইমস্ট্যাম্প জমানোর বদলে রেডিস হ্যাশে মাত্র ২টি সংখ্যা (`tokens` ও `last_updated`) রাখা (< ১০০ বাইট)।
- **রেস কন্ডিশন চিরতরে নির্মূল (Atomic Lua Script):** পাইথন ও রেডিসের মধ্যে মাল্টিপল রাউন্ড-ট্রিপ না করে রেডিস ইঞ্জিনের ভেতর সিঙ্গেল-থ্রেডেড অ্যাটমিক অপারেশনে টোকেন রিফিল ও কনজিউম করা।
- **মসৃণ ট্রাফিক শেপিং (Traffic Shaping):** শান্ত সময়ে জমানো টোকেন দিয়ে বাস্ট হ্যান্ডেল করা, আবার টানা চাপ থাকলে রিফিল রেটে (r) গতি বেঁধে ফেলা।

---

## ২. বাস্তব জীবনের রূপক: পানির কল, বালতি ও মেলায় নাগরদোলার টিকিট

কল্পনা করুন, একটি গ্রামীণ মেলার প্রবেশদ্বারে একটি নাগরদোলা রয়েছে। নাগরদোলায় চড়ার জন্য কাউন্টারে একটি **বালতি (Bucket)** রাখা আছে:

1. **বালতির ধারণক্ষমতা (Capacity $C = ৫$):**
   বালতিতে সর্বোচ্চ ৫টি মার্বেল বা টিকিট রাখা সম্ভব।

2. **পানির ফোঁটার মতো টিকিট রিফিল (Refill Rate $r = ১$টি/সেকেন্ড):**
   কাউন্টারের ওপর একটি স্বয়ংক্রিয় পাইপ রয়েছে যা থেকে প্রতি ১ সেকেন্ড পর পর ঠিক ১টি করে টিকিট টুপ করে বালতিতে পড়ে। বালতি পূর্ণ হয়ে গেলে অতিরিক্ত টিকিট নিচে পড়ে যায় (অর্থাৎ টোকেন সংখ্যা কখনোই ৫-এর বেশি হতে পারে না)।

3. **গ্রাহকের বাস্ট আগমন (Burst Traffic):**
   সকালে একদল বন্ধু এসে এক সেকেন্ডেই ৫টি টিকিট তুলে নিল (Burst Allowed)। বালতি মুহূর্তে শূন্য হয়ে গেল।

4. **শূন্য বালতি ও থ্রটলিং (Throttling / 429):**
   ৬ষ্ঠ বন্ধুটি সাথে সাথে হাত বাড়াল টিকিট নিতে। কাউন্টার মাস্টার বললেন: *"বালতি তো খালি! পরের টিকিটের জন্য তোমাকে ঠিক ১ সেকেন্ড অপেক্ষা করতে হবে (`Retry-After: 1`)"*।

5. **সময়ের সাথে অলস রিফিল (Lazy Dynamic Refill):**
   কাউন্টার মাস্টারকে প্রতি সেকেন্ডে সেকেন্ডে বালতি পাহারা দিতে হয় না। যখনই কোনো গ্রাহক আসে, মাস্টার ঘড়ির দিকে তাকান: *"আগেরবার টিকিট নিয়েছিল ১২:০০:০০ টায়, এখন বাজে ১২:০০:০২। অর্থাৎ ২ সেকেন্ড পার হয়েছে। তাহলে ২ সেকেন্ডে ২টি নতুন টিকিট পড়েছে!"* — এভাবে অলসভাবে (Lazily) মুহূর্তের মধ্যে হিসাব করে টিকিট দিয়ে দেওয়া হয়।

---

## ৩. প্রোডাকশন সিস্টেমের বিপর্যয় ও পেছনের গল্প (The "Why" Behind The Architecture)

### বিপর্যয় ১: মাল্টি-নোড রেস কন্ডিশনে কোটা ওভারগ্রান্ট

যদি লুয়া স্ক্রিপ্ট ব্যবহার না করে পাইথনের কোড দিয়ে `HMGET` করে রিফিল হিসাব করা হতো এবং পরে `HMSET` দিয়ে সেভ করা হতো, তবে কী হতো?

```
T=0ms: Pod-A পড়ে: tokens=1 (বালতিতে ১টি টোকেন বাকি)
T=0ms: Pod-B পড়ে: tokens=1 (একই সময়ে!)

T=1ms: Pod-A লেখে: tokens=0 → "সফল! অনুমতি দিলাম।"
T=1ms: Pod-B লেখে: tokens=0 → "সফল! অনুমতি দিলাম।"

ফলাফল: ১টি টোকেন থেকে ২টি রিকোয়েস্ট পাস হয়ে গেল!
```

সার্ভারে যখন ১০০টি কনকারেন্ট রিকোয়েস্ট একই মিলিসেকেন্ডে ঢুকত, সবাই দেখত বালতিতে ১টি টোকেন অবশিষ্ট আছে। ১০০টি রিকোয়েস্টই নিজেদের সফল ঘোষণা করে ডাটাবেসে ঢুকে পড়ত — কোটা সম্পূর্ণ ফাঁকি! **Redis Lua Script** রেডিসের একক থ্রেডে চলে বলে এই race window শারীরিকভাবে অস্তিত্বহীন।

### বিপর্যয় ২: মেমোরি ব্লোট ও OOM ক্র্যাশ (Sliding Window Memory Exhaustion)

স্লাইডিং উইন্ডো লগে ১০,০০০ ক্লায়েন্ট যদি প্রতি সেকেন্ডে ১০০টি করে রিকোয়েস্ট পাঠায়:

```
Redis ZSET এন্ট্রি = 10,000 clients × 100 req/s × 60s window
                   = 60,000,000 timestamp entries
                   ≈ কয়েক গিগাবাইট RAM!
```

কয়েক ঘণ্টার মধ্যে Redis-এর মেমোরি ফুল হয়ে OOM (Out Of Memory) ক্র্যাশে পুরো সিস্টেম ডাউন হয়ে যেত।

**Token Bucket-এ একই লোডে:** ১০,০০০ ক্লায়েন্ট × ২ সংখ্যা × ৮ বাইট ≈ মাত্র **১৬০ KB** — অপরিবর্তিত!

### বিপর্যয় ৩: `EVAL` দিয়ে নেটওয়ার্ক ব্যান্ডউইথ নষ্ট

প্রতিটি রিকোয়েস্টে সম্পূর্ণ Lua স্ক্রিপ্ট (≈৫০০ বাইট) পাঠানো হলে:
```
1,000,000 req/day × 500 bytes = 500 MB/day নেটওয়ার্ক ওভারহেড
```
`EVALSHA` দিয়ে শুধু ৪০ অক্ষরের SHA হেক্স পাঠানো হয়, নেটওয়ার্ক ওভারহেড **৯২% কম**।

---

## ৪. আর্কিটেকচার ওয়াকথ্রু (Architecture Deep Dive)

### ৪.১ রেডিস লুয়া স্ক্রিপ্ট — অ্যাটমিক ইঞ্জিন

```lua
local key = KEYS[1]
local capacity = tonumber(ARGV[1])
local refill_rate = tonumber(ARGV[2])
local requested = tonumber(ARGV[3])
local now = tonumber(ARGV[4])

-- ১. রেডিস হ্যাশ থেকে বর্তমান টোকেন ও সর্বশেষ আপডেটের সময় পড়া
local data = redis.call('HMGET', key, 'tokens', 'last_updated')
local tokens = tonumber(data[1])
local last_updated = tonumber(data[2])

-- ২. প্রথমবার হলে বালতি পূর্ণ করে শুরু
if not tokens or not last_updated then
    tokens = capacity
    last_updated = now
else
    -- ৩. অতিবাহিত সময় অনুযায়ী নতুন টোকেন রিফিল (ক্যাপাসিটির বেশি হবে না)
    local delta = math.max(0, now - last_updated)
    tokens = math.min(capacity, tokens + (delta * refill_rate))
    last_updated = now
end

local ttl = math.ceil(capacity / refill_rate) + 2

-- ৪. পর্যাপ্ত টোকেন থাকলে কেটে নিয়ে অনুমতি
if tokens >= requested then
    local new_tokens = tokens - requested
    redis.call('HMSET', key, 'tokens', tostring(new_tokens), 'last_updated', tostring(now))
    redis.call('EXPIRE', key, ttl)
    return {1, tostring(new_tokens), "0"}
else
    -- ৫. টোকেন কম থাকলে retry_after হিসাব করে রিজেক্ট
    local retry_after = (requested - tokens) / refill_rate
    redis.call('HMSET', key, 'tokens', tostring(tokens), 'last_updated', tostring(now))
    redis.call('EXPIRE', key, ttl)
    return {0, tostring(tokens), tostring(retry_after)}
end
```

**কেন এটি ১০০% রেস-ফ্রি?**
Redis-এর event loop single-threaded। Lua script চলার সময় অন্য কোনো client command execute হতে পারে না — এটি OS-level atomic guarantee, application lock নয়।

### ৪.২ `RateLimiterService` — `EVALSHA` + `NOSCRIPT` Self-Healing ([`app/services/rate_limiter_service.py`](file:///e:/FastApi1/app/services/rate_limiter_service.py))

```python
async def check_token_bucket(
    self,
    key: str,
    capacity: float,
    refill_rate: float,
    requested: float = 1.0,
    now: float | None = None,
) -> tuple[bool, float, float]:
    """
    Returns: (is_throttled, remaining_tokens, retry_after_seconds)
    """
    current_time = time.time() if now is None else now
    redis_key = f"ratelimit:tokenbucket:{key}"
    sha = await self._get_or_load_token_bucket_script()

    try:
        # EVALSHA: শুধু 40-char SHA পাঠানো — নেটওয়ার্ক পেলোড ন্যূনতম
        res = await self._redis.evalsha(
            sha, 1, redis_key, capacity, refill_rate, requested, current_time
        )
    except Exception as exc:
        # Redis restart-এ script cache মুছলে → স্বয়ংক্রিয় রি-লোড (NOSCRIPT self-heal)
        if "NOSCRIPT" in str(exc):
            self._token_bucket_sha = str(await self._redis.script_load(self.TOKEN_BUCKET_LUA))
            res = await self._redis.evalsha(
                self._token_bucket_sha, 1, redis_key, capacity, refill_rate, requested, current_time
            )
        else:
            raise

    allowed = int(res[0]) == 1
    tokens_remaining = float(res[1])
    retry_after = float(res[2])
    return not allowed, tokens_remaining, retry_after
```

### ৪.৩ রাউটার এন্ডপয়েন্ট — RFC 6585 হেডার ([`app/routers/rate_limiter_router.py`](file:///e:/FastApi1/app/routers/rate_limiter_router.py))

```python
@router.post("/token-bucket/check")
async def check_token_bucket_endpoint(
    key: str = Query(...),
    capacity: float = Query(default=5.0, ge=1.0),
    refill_rate: float = Query(default=1.0, ge=0.1),
    service: RateLimiterService = Depends(get_rate_limiter_service),
) -> JSONResponse:
    is_throttled, tokens, retry_after = await service.check_token_bucket(
        key=key, capacity=capacity, refill_rate=refill_rate
    )
    if is_throttled:
        return JSONResponse(
            status_code=429,
            content={"detail": "Rate limit exceeded", "retry_after": retry_after},
            headers={
                "Retry-After": str(int(retry_after) + 1),
                "X-RateLimit-Limit": str(int(capacity)),
                "X-RateLimit-Remaining": "0",
            },
        )
    return JSONResponse(
        status_code=200,
        content={"allowed": True, "tokens_remaining": tokens},
        headers={"X-RateLimit-Remaining": str(int(tokens))},
    )
```

---

## ৫. ডিএসএ ও গাণিতিক মেকানিক্স

### সূত্রাবলী:

1. **অতিবাহিত সময় (Elapsed Time):**
   $$\Delta t = \max(0, T_{\text{now}} - T_{\text{last}})$$

2. **নতুন রিফিল ব্যালেন্স (Refilled Balance):**
   $$B_{\text{refilled}} = \min(C,\ B_{\text{stored}} + \Delta t \times r)$$

3. **অপেক্ষার সময় (Retry-After Duration):**
   $$\text{Retry-After} = \frac{\text{requested} - B_{\text{refilled}}}{r}$$

### বিগ-ও জটিলতা বিশ্লেষণ:

| মেট্রিক | মান | গাণিতিক নিশ্চয়তা |
| :--- | :--- | :--- |
| **টাইম কমপ্লেক্সিটি** | $\mathcal{O}(1)$ | HMGET + Lua arithmetic + HMSET — সবই constant-time |
| **স্পেস কমপ্লেক্সিটি** | $\mathcal{O}(1)$ | প্রতি ক্লায়েন্টে শুধুমাত্র ২টি float (< ১০০ বাইট) |
| **নেটওয়ার্ক পেলোড** | ৪০ বাইট | `EVALSHA` SHA1 হেক্স — স্ক্রিপ্ট বডি পুনরায় পাঠানো হয় না |

---

## ৬. টোকেন বাকেট বনাম স্লাইডিং উইন্ডো তুলনা

| বৈশিষ্ট্য | Token Bucket | Sliding Window Log |
|-----------|-------------|-------------------|
| **স্পেস কমপ্লেক্সিটি** | $\mathcal{O}(1)$ per client | $\mathcal{O}(N)$ — N = window requests |
| **বাস্ট অনুমতি** | ✅ হ্যাঁ (capacity পর্যন্ত) | ❌ না (কঠোর সমতা) |
| **মেমোরি (১M client)** | ≈ ১০০ MB | ≈ কয়েক GB |
| **রেস কন্ডিশন রিস্ক** | Lua Atomicity দিয়ে শূন্য | Pipeline-এ সম্ভব |
| **কখন আদর্শ** | API Gateway, ব্যবহারকারী UI | পেমেন্ট, ফিনান্সিয়াল |

---

## ৭. টেস্ট রেজাল্ট সারমর্ম

```
tests/test_token_bucket_rate_limiter.py — সব টেস্ট পাস ✅
- test_burst_traffic_allowed_up_to_capacity
- test_sixth_request_throttled_with_retry_after
- test_tokens_refill_after_elapsed_time
- test_concurrent_requests_exactly_one_winner (race condition proof)
- test_noscript_recovery_self_heals
```

---

## ৮. ইন্টারভিউ ও ভাইভা প্রশ্ন

**প্রশ্ন ১: স্লাইডিং উইন্ডো লগ এবং টোকেন বাকেটের মধ্যে মূল পার্থক্য কী এবং কখন কোনটি বেছে নেবেন?**

*আদর্শ উত্তর:*
স্লাইডিং উইন্ডো লগ প্রতিটি রিকোয়েস্টের টাইমস্ট্যাম্প ZSET-এ সংরক্ষণ করে — $\mathcal{O}(N)$ স্পেস কিন্তু ১০০% মসৃণ রেট। টোকেন বাকেট কেবল ২টি সংখ্যা রাখে — $\mathcal{O}(1)$ স্পেস এবং ক্লায়েন্টকে ক্যাপাসিটি পর্যন্ত বাস্ট করতে দেয়। উচ্চ ট্রাফিকের ক্লাউড গেটওয়েতে মেমোরি বাঁচাতে টোকেন বাকেট আদর্শ; কঠোর আর্থিক রেট কন্ট্রোলে স্লাইডিং উইন্ডো উপযুক্ত।

**প্রশ্ন ২: রেডিসে লুয়া স্ক্রিপ্ট কেন ব্যবহার করা হয় এবং `EVAL` বনাম `EVALSHA`-এর সুবিধা কী?**

*আদর্শ উত্তর:*
রেডিসে একাধিক কমান্ডের মাঝে ডেটা নির্ভরতা থাকলে client-side pipeline-এ race condition হতে পারে। Lua script রেডিসের single thread-এ একটি একক transaction হিসেবে রান হয় — ১০০% atomic। `EVAL` প্রতিবার পুরো script পাঠায় (bandwidth waste); `EVALSHA` রেডিসে script cache করে শুধু ৪০-char SHA1 পাঠায় — নেটওয়ার্ক লেটেন্সি ন্যূনতম এবং `NOSCRIPT` ফলব্যাকে self-healing।

**প্রশ্ন ৩: `NOSCRIPT` error কেন হয় এবং কীভাবে handle করবেন?**

*আদর্শ উত্তর:*
Redis server restart হলে in-memory script cache flush হয়। তখন `EVALSHA` করলে Redis `NOSCRIPT No matching script` error দেয়। সমাধান: `except` block-এ `"NOSCRIPT" in str(exc)` check করে `script_load()` দিয়ে script পুনরায় cache করা এবং নতুন SHA দিয়ে আবার `EVALSHA` করা — এটি zero downtime self-healing।

---

## ৯. মূল শিক্ষা (Key Takeaways)

1. **Token Bucket = $\mathcal{O}(1)$ মেমোরি ও বাস্ট ট্রাফিক আর্কিটেকচার।** শত কোটি ইউজারের রেট লিমিট RAM-খরচ ছাড়াই।
2. **Redis Lua Script = জিরো রেস কন্ডিশন।** Redis-এর single thread-এই আমাদের atomic guarantee — কোনো application-level lock নয়।
3. **`EVALSHA` + `NOSCRIPT` ফলব্যাক = সাব-মিলিসেকেন্ড নেটওয়ার্ক ও সেলফ-হিলিং রেজিলিয়েন্স।**
4. **Lazy Refill (অলস রিফিল) = কোনো Background Timer নেই।** প্রতিটি রিকোয়েস্টে $\Delta t \times r$ ফর্মুলায় তৎক্ষণাৎ হিসাব করা — timer thread বা cron job-এর প্রয়োজন নেই।
5. **TTL = `ceil(capacity / refill_rate) + 2`।** নিষ্ক্রিয় client-এর key স্বয়ংক্রিয়ভাবে মুছে যায় — zero memory leak।
