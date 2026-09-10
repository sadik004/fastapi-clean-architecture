# ডে ৬৪: গ্রেসফুল ডিগ্রেডেশন ও মাল্টি-টিয়ার ফলব্যাক আর্কিটেকচার (সার্ভিস ডিগ্রেডেশনে স্টেল/ক্যাশড/ডিফল্ট ডাটা পরিবেশন)

---

## ১. আমরা কী বানিয়েছি? (Mandatory Section 1)

আজকে **ডে ৬৪**-তে আমরা নেটফ্লিক্স ও হাই-স্কেল সাইট রিলায়েবিলিটি ইঞ্জিনিয়ারিং (SRE) স্ট্যান্ডার্ড অনুযায়ী প্রোডাকশন-গ্রেড **গ্রেসফুল ডিগ্রেডেশন ও মাল্টি-টিয়ার ফলব্যাক ইঞ্জিন (Graceful Degradation & Multi-Tier Fallback Engine)** আর্কিটেকচার বাস্তবায়ন করেছি। 

অক্সিলিয়ারি বা নন-ক্রিটিক্যাল মাইক্রোসার্ভিস (যেমন: পারসোনালাইজড প্রোডাক্ট রেকমেন্ডেশন ইঞ্জিন, অ্যাডস টার্গেটিং, সোশ্যাল উইজেটস, রিল্যাটেড কন্টেন্ট) ক্র্যাশ বা টাইমআউট করলেও যেন আমাদের এপিআই গ্রাহককে কোনোভাবেই `HTTP 500 Internal Server Error` না দেখায়, তা নিশ্চিত করার জন্য আমরা ৩-স্তরের ফলব্যাক ল্যাডার তৈরি করেছি:

1. **মাল্টি-টিয়ার ফলব্যাক ইঞ্জিন (`app/core/resilience/fallback.py`)**:
   - `DegradationLevel` এনুমারেটর: `PRIMARY` (লাইভ ও ফ্রেশ ডাটা), `STALE_CACHE` (রেডিস ক্যাশ থেকে প্রাপ্ত পুরনো ডাটা), এবং `STATIC_DEFAULT` (হার্ডকোডেড সেফ কিউরেটেড ডিফল্ট ডাটা)।
   - ৩-স্তরের প্রগ্রেসিভ এক্সিকিউশন ল্যাডার:
     - **টিয়ার ১ (Tier 1 - Primary Live Call)**: লাইভ এআই রেকমেন্ডেশন মাইক্রোসার্ভিস বা প্রাইমারি ফাংশন কল। এটি সফল হলে রেসপন্স রিটার্ন করে এবং ব্যাকগ্রাউন্ডে নন-ব্লকিং নিরাপদে রেডিস ক্যাশে আপডেট পাঠায় (`_safe_cache_set`)। রেডিস ডাউন থাকলেও প্রাইমারি এক্সিকিউশন কখনো ব্যর্থ হয় না।
     - **টিয়ার ২ (Tier 2 - Stale Redis Cache Fallback)**: প্রাইমারি সার্ভিস কোনো এক্সেপশন রেইজ করলে (`TimeoutError`, `ServiceUnavailableException`) ইঞ্জিন তাৎক্ষণিকভাবে লোকাল বা সেন্ট্রালাইজড রেডিস থেকে ব্যবহারকারীর শেষ সফল ক্যাশড ডাটা রিড করে পরিবেশন করে।
     - **টিয়ার ৩ (Tier 3 - Static Safe Default Fallback)**: প্রাইমারি ডাউন এবং রেডিস ক্যাশেও ডাটা না থাকলে (বা রেডিস ক্লাস্টার সম্পূর্ণ ক্র্যাশ করলে) ইঞ্জিন মেমোরিতে থাকা কিউরেটেড গ্লোবাল ডিফল্ট ডাটা পরিবেশন করে। ক্লায়েন্ট `HTTP 200 OK` সহ কার্যক্ষম রেসপন্স পায়—**জিরো ৫০০ ক্র্যাশ গ্যারান্টি**।
2. **টেলিমেট্রি এইচটিটিপি রেসপন্স হেডার্স**:
   - `X-Degraded-Mode`: `FALSE` (টিয়ার ১ প্রাইমারির ক্ষেত্রে) অথবা `TRUE` (টিয়ার ২ বা ৩ এর ক্ষেত্রে)।
   - `X-Degradation-Level`: `PRIMARY` | `STALE_CACHE` | `STATIC_DEFAULT`। ফ্রন্টএন্ড বা ক্লায়েন্ট এপিআই গেটওয়ে এই হেডার দেখে ব্যবহারকারীকে "Showing cached results" বা "Popular products" জাতীয় সাবলীল নোটিফিকেশন প্রদর্শন করতে পারে।
3. **রেজিলিয়েন্ট প্রোডাক্ট রেকমেন্ডেশন সার্ভিস (`app/services/recommendation_service.py`)**:
   - `ProductRecommendationService`: ব্যবহারকারীর জন্য পারসোনালাইজড রেকমেন্ডেশন গণনা করে এবং সিমুলেটেড ফেইলিউরে স্বয়ংক্রিয়ভাবে টিয়ার ২ ও টিয়ার ৩-এ গ্রেসফুলি নেমে যায়।
   - `seed_user_cache`: ইন্টিগ্রেশন টেস্টিং এবং ক্যাশ ওয়ার্মিংয়ের জন্য ডেডিকেটেড ক্যাশ প্রি-পপুলেশন মেকানিজম।
4. **এপিআই রাউটিং ও টেস্ট স্যুট (`app/routers/resilience_router.py` & `tests/test_graceful_degradation.py`)**:
   - `GET /resilience/recommendations/{user_id}`: সম্পূর্ণ মাল্টি-টিয়ার রেজিলিয়েন্স ও হেডার সহ রেকমেন্ডেশন এপিআই।
   - `POST /resilience/recommendations/seed-cache`: ফলব্যাক ক্যাশ প্রি-পপুলেশন এন্ডপয়েন্ট।
   - ১২টি ইউনিট ও ইন্টিগ্রেশন টেস্ট যা প্রতিটি টিয়ার, ক্যাশ ফেইলিউর রেজিলিয়েন্স ও হেডারের কার্যকারিতা নিশ্চিত করে।

---

## ২. 📌 ব্যবহৃত DSA ও রেজিলিয়েন্স অ্যালগরিদমের নাম (Mandatory Section 2)

- **প্রগ্রেসিভ মাল্টি-টিয়ার ফলব্যাক ল্যাডার (Progressive Multi-Tier Fallback Ladder — Netflix Chaos Engineering Pattern)**
- **হ্যাশ ম্যাপ ও কি-ভ্যালু ডিকশনারি লুকআপ ($\mathcal{O}(1) \text{ Hash Map / Distributed In-Memory Cache Lookup via Redis}$)**
- **নন-ব্লকিং ক্যাশ রাইট আইসোলেশন ও ডিফেন্সিভ শিল্ডিং (Non-Interfering Asynchronous Cache Update with Exception Shielding)**
- **গ্রেসফুল ডিগ্রেডেশন ও ক্লায়েন্ট সাইড টেলিমেট্রি প্রোটোকল (Degraded Mode Telemetry via HTTP Headers `X-Degraded-Mode` & `X-Degradation-Level`)**

---

## ৩. 🚀 প্রোডাকশনে ঠিক কখন ব্যবহার করব? (Mandatory Section 3)

1. **ই-কমার্স হোমপেজ ও প্রোডাক্ট রেকমেন্ডেশন উইজেটস (Product Recommendations & Personalization):**
   - কোনো ই-কমার্স ওয়েবসাইটের হোমপেজে ব্যবহারকারীর জন্য পারসোনালাইজড রেকমেন্ডেশন দেখানোর ব্যাকএন্ড এআই সার্ভিস ডাউন থাকলেও পুরো হোমপেজ ক্র্যাশ করা যাবে না। স্টেল ক্যাশ বা ট্রেন্ডিং প্রোডাক্ট দেখিয়ে ব্যবসা সচল রাখতে হবে।
2. **সার্চ সাজেশন ও অটো-কমপ্লিট (Search Autocomplete & Dynamic Suggestions):**
   - ডায়নামিক সার্চ সাজেস্টার ক্লাস্টার ডাউন থাকলে পূর্বে সেভ করা টপ কিওয়ার্ড ডিফল্ট হিসেবে ক্লায়েন্টকে দেখাতে হবে।
3. **ওয়েদার ও স্টক মার্কেট উইজেট (External Feed Aggregation):**
   - আবহাওয়া বা শেয়ার বাজারের বাইরের এপিআই যদি সাময়িক সাড়া না দেয়, ব্যবহারকারীকে শেষ জানা ডাটা ("Last updated 15m ago") দেখানোই সর্বোত্তম ইউজার এক্সপেরিয়েন্স।
4. **বিজ্ঞাপন ও স্পন্সরড কন্টেন্ট ডিসপ্লে (Ad Serving Infrastructure):**
   - অ্যাড সার্ভার ডাউন থাকলে ফাঁকা সাদা স্ক্রিন বা ইন্টারনাল সার্ভার এরর না দিয়ে জেনেরিক প্ল্যাটফর্ম ব্যানার ডিসপ্লে করা।

---

## ৪. 🎯 কী কারণে বা কোন পরিস্থিতিতে ব্যবহার করব? (Why & Technical Triggers)

1. **ব্লাস্ট রেডিয়াস (Blast Radius) সংকোচন:**
   - একটি মনোলিথিক বা মাইক্রোসার্ভিস আর্কিটেকচারে সহায়ক (Auxiliary) সার্ভিস কখনো ক্রিটিক্যাল পাথ (Core Path: Checkout, Login, Payment) ভাঙতে পারে না। রেকমেন্ডেশন ব্যর্থ হলে চেকআউট পেজ আটকে থাকা মারাত্মক অপেশাদারিত্ব।
2. **জিরো এইচটিটিপি ৫০০ ত্রুটি গ্যারান্টি (Zero 500 Outage Guarantee):**
   - ফ্রন্টএন্ড ব্যবহারকারীর কাছে এরর মোডাল আসার চেয়ে কিছুটা পুরনো ডাটা পাওয়া ১০০০ গুণ বেশি গ্রহণযোগ্য।
3. **ক্যাশ ফেইলিউর টলারেন্স (Silent Cache Degradation):**
   - প্রাইমারি এক্সিকিউশন সফল হলেও যদি ক্যাশ সার্ভারে লেখার সময় নেটওয়ার্ক টাইমআউট হয়, সেই ক্যাশ এরর কখনোই প্রাইমারি গ্রাহককে প্রভাবিত করা যাবে না।

---

## ৫. আর্কিটেকচার ও ডাটা ফ্লো ডায়াগ্রাম (Multi-Tier Fallback Ladder)

```
                       [Incoming Client Request]
                                  │
                                  ▼
                 ┌─────────────────────────────────┐
                 │    Tier 1: Primary Live Call    │
                 │  (ML Model / External Service)  │
                 └────────────────┬────────────────┘
                                  │
                    ┌─────────────┴─────────────┐
             Success│                           │Fails (Exception)
                    ▼                           ▼
      ┌───────────────────────────┐    ┌─────────────────────────────────┐
      │  Return Data              │    │     Tier 2: Stale Redis Cache   │
      │  Level: PRIMARY           │    │       (recs:user:{user_id})     │
      │  X-Degraded-Mode: FALSE   │    └────────────────┬────────────────┘
      │  [Async Update Cache]     │                     │
      └───────────────────────────┘       ┌─────────────┴─────────────┐
                                   Hit /  │                           │ Miss /
                                   Success▼                           ▼ Redis Down
                               ┌──────────────────────┐    ┌──────────────────────────┐
                               │ Return Cached Data   │    │ Tier 3: Static Default   │
                               │ Level: STALE_CACHE   │    │ (DEFAULT_TRENDING_PRODS) │
                               │ X-Degraded-Mode: TRUE│    │ Level: STATIC_DEFAULT    │
                               └──────────────────────┘    │ X-Degraded-Mode: TRUE    │
                                                           │ HTTP Status: 200 OK      │
                                                           └──────────────────────────┘
```

---

## ৬. কোর কোড ইমপ্লিমেন্টেশন ও ব্যাখ্যা

### ১. `FallbackEngine` কোর লজিক (`app/core/resilience/fallback.py`)
```python
async def execute_with_fallback(
    self,
    primary_func: Callable[[], Awaitable[T]],
    fallback_cache_key: str | None,
    static_default: T,
    cache_ttl: int = 3600,
    serialize: Callable[[T], str] | None = None,
    deserialize: Callable[[str], T] | None = None,
) -> tuple[T, DegradationLevel]:
    # -------------------------------------------------------------
    # Tier 1: Primary Live Execution
    # -------------------------------------------------------------
    try:
        data = await primary_func()
        # Non-blocking defensive cache write (never crashes primary)
        if fallback_cache_key:
            await self._safe_cache_set(
                fallback_cache_key, data, cache_ttl, serialize
            )
        return data, DegradationLevel.PRIMARY
    except Exception as primary_exc:
        logger.warning("Primary failed (%s). Stepping down to Tier 2...", primary_exc)

    # -------------------------------------------------------------
    # Tier 2: Stale Cache Fallback
    # -------------------------------------------------------------
    if fallback_cache_key:
        cached_data = await self._safe_cache_get(fallback_cache_key, deserialize)
        if cached_data is not None:
            return cached_data, DegradationLevel.STALE_CACHE

    # -------------------------------------------------------------
    # Tier 3: Static Safe Default Fallback
    # -------------------------------------------------------------
    return static_default, DegradationLevel.STATIC_DEFAULT
```

### ২. টেলিমেট্রি রেসপন্স হেডার ইনজেকশন (`app/routers/resilience_router.py`)
```python
result, level = await service.get_personalized_recommendations(
    user_id=user_id,
    simulate_failure=simulate_failure,
)

response.headers["X-Degraded-Mode"] = "FALSE" if level == DegradationLevel.PRIMARY else "TRUE"
response.headers["X-Degradation-Level"] = level.value

return RecommendationResponse(**result)
```

---

## ৭. টাইম ও স্পেস কমপ্লেক্সিটি অ্যানালাইসিস (DSA Rigor)

| অপারেশন / টিয়ার | টাইম কমপ্লেক্সিটি | স্পেস কমপ্লেক্সিটি | অ্যালগরিদমিক ব্যাখ্যা |
| :--- | :--- | :--- | :--- |
| **Tier 1: Primary Execution** | $\mathcal{O}(T_{\text{primary}})$ | $\mathcal{O}(S_{\text{response}})$ | প্রাইমারি সার্ভিসের নেটওয়ার্ক ল্যাটেন্সি ও মেমোরি রেসপন্স সাইজ। |
| **Tier 1: Cache Set** | $\mathcal{O}(1)$ | $\mathcal{O}(S_{\text{serialized}})$ | রেডিসে হ্যাশ লুকআপ/স্ট্রিং সেটিং নন-ব্লকিং ডিফেন্সিভ ট্রাই-ক্যাচ সহ। |
| **Tier 2: Stale Cache Get** | $\mathcal{O}(1)$ | $\mathcal{O}(S_{\text{serialized}})$ | রেডিস ইন-মেমোরি কি লুকআপ $\mathcal{O}(1)$ দ্রুততায় রেসপন্স সরবরাহ করে। |
| **Tier 3: Static Default** | $\mathcal{O}(1)$ | $\mathcal{O}(1)$ | পূর্বে সংজ্ঞায়িত মেমোরি রেফারেন্স সরাসরি রিটার্ন করা হয়, কোনো আই/ও ওভারহেড নেই। |

---

## ৮. অ্যান্টি-প্যাটার্ন ও কমন পিটফলস (Critical Traps & Fixes)

### অ্যান্টি-প্যাটার্ন ১: অক্সিলিয়ারি সার্ভিস ফেইলিউরে পুরো পেজে ৫০০ এরর দেখানো
```python
# ❌ FATAL ANTI-PATTERN: Secondary service outage breaks the entire view
try:
    recs = await external_recs_api.get_recommendations(user_id)
except Exception as exc:
    raise HTTPException(status_code=500, detail="Recommendation service error")
```
**প্রোডাকশন ফিক্স**: অক্সিলিয়ারি সার্ভিসকে সর্বদা ৩-স্তরের ফলব্যাকে মুড়িয়ে নিতে হবে। এরর রেইজ করার বদলে স্টেল ক্যাশ বা কিউরেটেড ডিফল্ট রিটার্ন করে `HTTP 200` দিতে হবে।

### অ্যান্টি-প্যাটার্ন ২: ক্যাশ রাইট এররে প্রাইমারি রেসপন্স ক্র্যাশ করা
```python
# ❌ BUGGY PATTERN: Cache server network glitch crashes client request
data = await primary_func()
await redis_client.set(cache_key, json.dumps(data)) # Crashes if Redis is down!
return data
```
**প্রোডাকশন ফিক্স**: ক্যাশ রাইট অপারেশনকে সর্বদা `_safe_cache_set` ট্রাই-ক্যাচ ব্লকে আটকে রাখতে হবে। ক্যাশ ব্যর্থ হলেও ক্লায়েন্ট তার লাইভ ডাটা সফলভাবে পাবে।

---

## ৯. প্রোডাকশন রুলস ও বেস্ট প্র্যাকটিসেস (Production Contract)

1. **টেলিমেট্রি স্বচ্ছতা (Observability Transparency)**: ফলব্যাক ডাটা পরিবেশন করলেও রেসপন্স হেডারে `X-Degraded-Mode: TRUE` এবং `X-Degradation-Level` পাঠাতে হবে যাতে ক্লায়েন্ট এবং এপিআই মনিটরিং টুলস সার্ভিস ডিগ্রেডেশন ডিটেক্ট করতে পারে।
2. **ক্যাশ ওয়ার্মিং ও টিটিএল পলিসি**: অক্সিলিয়ারি ক্যাশের টিটিএল অপেক্ষাকৃত দীর্ঘ রাখা উচিত (যেমন ১ ঘন্টা বা ২৪ ঘন্টা), যাতে সাময়িক ব্যাকএন্ড বিভ্রাটে গ্রাহক টিয়ার ২-তেই চমৎকার অভিজ্ঞতা পায়।
3. **মিউটেটিং বনাম কোয়েরি অপারেশন সতর্কতা**: ফলব্যাক প্যাটার্ন শুধুমাত্র ইডেমপোটেন্ট রিড (Queries/Views) অপারেশনে প্রযোজ্য। পেমেন্ট চার্জ বা মানি ট্রান্সফারের মতো ক্রিটিক্যাল রাইট অপারেশনে ফলব্যাক ডাটা তৈরি নিষিদ্ধ।

---

## ১০. সামারি ও রিফ্লেকশন (Daily Reflection)

আজকে ডে ৬৪-তে আমরা শিখেছি কীভাবে আধুনিক উচ্চ-স্কেলিং ওয়েব অ্যাপ্লিকেশনগুলো শত শত ব্যাকএন্ড সার্ভিসের ব্যর্থতার মধ্যেও ব্যবহারকারীর চোখের সামনে কখনো 'Broken Page' আসতে দেয় না। এই ৩-টিয়ার ল্যাডার রেজিলিয়েন্স ইঞ্জিন আমাদের এপিআই-এর নির্ভরযোগ্যতাকে বিশ্বের শীর্ষস্থানীয় টেক জায়ান্টদের মানে উন্নীত করেছে।
