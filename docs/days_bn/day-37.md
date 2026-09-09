# Day 37: ডিস্ট্রিবিউটেড স্লাইডিং উইন্ডো লগ রেট লিমিটার (Distributed Sliding Window Rate Limiter using Redis ZSET & Atomic Pipelines)

## 📌 ব্যবহৃত DSA-এর সুনির্দিষ্ট নাম:
**ডিস্ট্রিবিউটেড স্লাইডিং উইন্ডো লগ (Distributed Sliding Window Log via Redis ZSET & Atomic Multi/Exec Pipeline) (O(log N + M) প্রুনিং, O(1) উইন্ডো কাউন্ট)**

---

## 🚀 প্রোডাকশনে ঠিক কখন ব্যবহার করব? (When to use in Production)
- **মাল্টি-সার্ভার ক্লাস্টার এপিআই গেটওয়ে:** যেখানে ডকার বা কুবারনেটিসে একাধিক ফাস্টএপিআই কন্টেইনার লোড ব্যালান্সারের পেছনে চলে।
- **পেমেন্ট ও ওটিপি (OTP) গেটওয়ে সিকিউরিটি:** কোনো আক্রমণকারী যেন একাধিক সার্ভারকে ধোঁকা দিয়ে মিনিটে শত শত পেমেন্ট বা ওটিপি রিকোয়েস্ট না পাঠাতে পারে।
- **পাবলিক এপিআই টায়ার ও থ্রটলিং:** প্রতিটি আইপি বা এপিআই কি-র জন্য ক্লাস্টার-ওয়াইড নিখুঁত রিকোয়েস্ট লিমিট বলবৎ রাখা।

---

## 🎯 কী কারণে বা কোন পরিস্থিতিতে ব্যবহার করব? (Why to use / Technical Triggers)
- **ইন-মেমোরি রেট লিমিটারের অন্ধত্ব (Cross-Server Blindness) দূরীকরণ:** ৫টি সার্ভার থাকলে মেমোরি লিমিটিং ব্যর্থ হয়; সেন্ট্রাল রেডিস ZSET দিয়ে সব সার্ভারে অভিন্ন হিসাব রাখা।
- **ফিক্সড উইন্ডোর বাউন্ডারি স্পাইক ডিফেক্ট দূরীকরণ:** মিনিটের শুরুতে ও শেষে ডাবল ট্রাফিকের স্পাইক আটকে মিলি-সেকেন্ড অ্যাকুরেসি নিশ্চিত করা।
- **জিরো মেমোরি লিক গ্যারান্টি:** ZREMRANGEBYSCORE দিয়ে প্রতি রিকোয়েস্টেই পুরোনো টাইমস্ট্যাম্প মুছে ফেলা এবং EXPIRE দিয়ে নিষ্ক্রিয় ক্লায়েন্টের কি মুছে দেওয়া।

---

## ১. সহজ ভাষায় উপমা ও বাস্তব জীবনের গল্প: ৫ দরজার ব্যাংক ও চতুর ডাকাত দল

কল্পনা করুন, একটি সুরক্ষিত ব্যাংকের লকারে সাধারণ গ্রাহকরা প্রতি মিনিটে সর্বোচ্চ **১০ বার** প্রবেশ করতে পারেন। ব্যাংকের প্রধান ফটকে ৫টি ভিন্ন দরজা রয়েছে এবং প্রতি দরজায় একজন করে দারোয়ান নিযুক্ত। 

### ইন-মেমোরি রেট লিমিটারের অন্ধত্ব (Multi-Node Blindness):
যদি প্রতি দরোয়ানের হাতে একটি করে আলাদা খাতা (In-Memory Python Memory) থাকে এবং তারা নিজেদের মধ্যে কথা না বলে, তবে কী ঘটবে?
একজন চতুর ডাকাত বা অপব্যবহারকারী গ্রাহক ১ম দরোয়ানকে দিয়ে ১০ বার ঢুকল, তারপর দৌড়ে ২য় দরোয়ানে ১০ বার, ৩য় দরোয়ানে ১০ বার—এভাবে ৫টি দরজা ব্যবহার করে প্রতি মিনিটে ১০ বারের জায়গায় **৫০ বার** ব্যাংকে ঢুকে লকার খালি করে ফেলবে! প্রতিটি দারোয়ান ভাবছে গ্রাহক তো তার নিয়ম ভাঙেনি, কিন্তু ব্যাংকের কেন্দ্রীয় ভল্ট ততক্ষণে ধ্বংস!

এটিই হলো হরিজন্টালি স্কেলড ক্লাউড মাইক্রোসার্ভিসের (FastAPI / Kubernetes Pods / Gunicorn Workers) ইন-মেমোরি রেট লিমিটারের মূল অন্ধত্ব। অ্যাপ্লিকেশনের লোকাল মেমোরিতে রেট লিমিট রাখলে ক্লাস্টার স্কেল করার সাথে সাথে গ্রাহক অবাধে লিমিট বাইপাস করে ফেলে।

### ফিক্সড উইন্ডো বাকেট ট্র্যাজেডি (Boundary Burst Vulnerability):
এখন ব্যাংক কর্তৃপক্ষ সিদ্ধান্ত নিল কেন্দ্রীয় একটি ডিজিটাল কাউন্টার থাকবে, যা প্রতি ঘড়ির মিনিটের শুরুতে (যেমন ১২:০০:০০ থেকে ১২:০১:০০) শূন্য হয়ে যাবে।
ডাকাত দল এবার নতুন কৌশল নিল:
- তারা ১২:০০:৫৯ সেকেন্ডে (মিনিটের শেষ ১ সেকেন্ডে) দ্রুত ১০টি রিকোয়েস্ট পাঠাল।
- ঘড়ির কাঁটা ১২:০১:০০ ছুঁতেই কাউন্টার আবার রিসেট হয়ে শূন্য হলো।
- তারা ১২:০১:০১ সেকেন্ডে (নতুন মিনিটের ১ম সেকেন্ডে) আরও ১০টি রিকোয়েস্ট পাঠাল।

**ফলাফল:** মাত্র ২ সেকেন্ডের মধ্যে সিস্টেমে ২০টি রিকোয়েস্ট আঘাত করল ($2\times$ Traffic Burst)! সার্ভারের ডাটাবেস ক্র্যাশ করে গেল।

### সমাধান: সেন্ট্রালাইজড স্লাইডিং উইন্ডো লগ (Redis Sorted Set):
স্লাইডিং উইন্ডো কোনো ফিক্সড ঘড়ির ব্লকে কাজ করে না। এটি একটি চলমান সময়-সীমানা (Rolling Horizon)। গ্রাহক যখনই দরজায় টোকা দেবেন, দারোয়ান ঠিক বর্তমান মুহূর্ত ($T$) থেকে পেছনের ৬০ সেকেন্ড ($T - 60$) পর্যন্ত খাতা খুলে দেখবে মোট কয়টি স্ট্যাম্প রয়েছে। ৬০ সেকেন্ডের আগের স্ট্যাম্পগুলো ঝাড়ু দিয়ে মুছে ফেলা হবে। বর্তমান মুহূর্তে যদি মোট স্ট্যাম্প অনুমোদিত কোটা ছাড়িয়ে যায়, তবে তৎক্ষণাৎ গ্রাহককে ফিরিয়ে দেওয়া হবে এবং ঠিক কত সেকেন্ড পর সবচেয়ে পুরোনো স্ট্যাম্পটি মুছে যাবে, তার একটি টোকেন (`Retry-After`) ধরিয়ে দেওয়া হবে। আর প্রতিটি দরোয়ান কেন্দ্রীয়ভাবে Redis-এর সাথে একটিমাত্র **অ্যাটমিক পাইপলাইনে (Atomic Pipeline)** এই কাজ সম্পাদন করায় কোনো রেস কন্ডিশন ঘটা অসম্ভব!

---

## ২. টেকনিক্যাল ট্রিগার: কখন এবং কেন এটি ব্যবহার করতে আপনি বাধ্য?

একজন সফটওয়্যার আর্কিটেক্ট বা ব্যাকএন্ড ইঞ্জিনিয়ার নিম্নলিখিত পরিস্থিতিগুলোতে ডিস্ট্রিবিউটেড স্লাইডিং উইন্ডো লগ লিমিটার বাস্তবায়নে বাধ্য হন:

1. **হরিজন্টাল অটো-স্কেলিং মাইক্রোসার্ভিস ক্লাস্টার (Kubernetes HPA / Multi-Replica Pods):**  
   প্রডাকশন ক্লাস্টারে যখন একাধিক FastAPI পড বা ডকার কন্টেইনার চলে, তখন প্রতিটি পডের নিজস্ব লোকাল মেমোরি থাকে। লোড ব্যালেন্সার (Nginx / AWS ALB) যখন রাউন্ড-রবিন বা লিস্ট-কানেকশন পদ্ধতিতে ট্রাফিক ডিস্ট্রিবিউট করে, তখন ইন-মেমোরি লিমিটার সম্পূর্ণ ব্যর্থ হয়। ক্লাস্টারের সমস্ত নোডের জন্য একটি একক গ্লোবাল স্টেট স্টোর (Redis) বাধ্যতামূলক।

2. **বাউন্ডারি ট্রাফিক স্পাইক ও ডিডস প্রতিরোধ (Boundary Burst Mitigation):**  
   পেমেন্ট গেটওয়ে, ওটিপি সেন্ডিং সার্ভিস, বা ক্রিপ্টোকারেন্সি ট্রেডিং ইঞ্জিনে প্রতি মিনিটে নির্দিষ্ট কোটার চেয়ে দ্বিগুণ ট্রাফিক মুহূর্তের মধ্যে আঘাত করলে ব্যাংক অ্যাকাউন্ট বা ক্যাপিটাল ডাবল স্পেন্ডিং ঘটতে পারে। ফিক্সড উইন্ডো কাউন্টারে উইন্ডো পরিবর্তনের সীমানায় $2\times$ ট্রাফিক স্পাইক ঠেকানোর একমাত্র উপায় স্লাইডিং উইন্ডো।

3. **পাবলিক ওপেন API ও থার্ড-পার্টি রেট কোটা (Stripe / GitHub Style RFC Compliance):**  
   যখন কোনো এন্টারপ্রাইজ পাবলিক API তৈরি করা হয়, তখন গ্রাহককে সুনির্দিষ্ট HTTP 429 Too Many Requests রেসপন্সের সাথে নির্ভুল `Retry-After`, `X-RateLimit-Limit`, এবং `X-RateLimit-Remaining` হেডার প্রদান করতে হয়, যাতে ক্লায়েন্ট SDK স্বয়ংক্রিয়ভাবে ব্যাকঅফ করতে পারে।

4. **ব্রুট-ফোর্স ও স্ক্র্যাপিং অ্যাটাক ডিফেন্স (Credential Stuffing & Search Flooding):**  
   লগইন এন্ডপয়েন্ট বা জটিল ডাটাবেস সার্চ এন্ডপয়েন্টে বট ট্রাফিক বা ডিস্ট্রিবিউটেড স্ক্র্যাপারদের থামাতে প্রতি ক্লায়েন্ট আইপি বা API-কী অনুযায়ী সুনির্দিষ্ট টাইমস্ট্যাম্প লগ ট্র্যাক করা অপরিহার্য।

---

## ৩. প্রডাকশন সিনারিও ও বাস্তব অ্যাপ্লিকেশন

| প্রডাকশন সিনারিও | চ্যালেঞ্জ | স্লাইডিং উইন্ডো সমাধান |
| :--- | :--- | :--- |
| **১. আর্থিক পেমেন্ট গেটওয়ে (Stripe / bKash Checkout)** | উইন্ডো বাউন্ডারিতে ডাবল ক্লিক বা কনকারেন্ট পে রিকোয়েস্টে জোড়া ট্রানজ্যাকশন ইনিশিয়েট হওয়া। | Redis ZSET পাইপলাইন ন্যানোসেকেন্ডে কনকারেন্ট রেস আটকে অতিরিক্ত রিকোয়েস্ট ড্রপ করে এবং `Retry-After` প্রদান করে। |
| **২. এসএমএস ও ওটিপি ভেরিফিকেশন API** | টেলকো গেটওয়েতে বিলিং বম্বিং ও স্প্যামিং রিকোয়েস্ট পাঠিয়ে কোম্পানির লাখ লাখ টাকার ক্ষতিসাধন। | প্রতি ফোন নম্বরে বা আইপিতে ১০ মিনিটে সর্বোচ্চ ৩টি রিকোয়েস্টের হার্ড লিমিট নিশ্চিত করা। |
| **৩. এআই এলএলএম ইনফারেন্স এপিআই (LLM Tokens/Sec)** | এক ক্লায়েন্ট এক সেকেন্ডে ১০০টি ভারী জেনারেটিভ কুয়েরি পাঠিয়ে GPU সার্ভার ব্লক করে রাখা। | ক্লায়েন্ট API Key-এর বিপরীতে প্রতি ৬০ সেকেন্ডের চলমান উইন্ডোতে টোকেন/রিকোয়েস্ট থ্রটলিং। |
| **৪. ই-কমার্স ফ্ল্যাশ সেল স্টক লক API** | ফ্ল্যাশ সেলের প্রথম ১০ সেকেন্ডে বট স্ক্রিপ্ট দিয়ে মিলিসেকেন্ডের ব্যবধানে হাজার হাজার স্টক ব্লক করা। | প্রতি আইপিতে মিলি-সেকেন্ড নিখুঁত স্লাইডিং টাইমস্ট্যাম্প নিশ্চিত করে বট ব্লক করা। |

---

## ৪. ডেটা স্ট্রাকচার ও অ্যালগরিদম গভীর ব্যবচ্ছেদ

### ডেটা স্ট্রাকচার: Redis Sorted Set (ZSET)
Redis-এর Sorted Set অভ্যন্তরীণভাবে দুটি ডেটা স্ট্রাকচারের সমন্বয়ে গঠিত:
1. **স্কিপলিস্ট (SkipList):** ব্যালেন্সড ট্রি-এর মতো $O(\log N)$ সময়ে স্কোর অনুযায়ী উপাদান সাজিয়ে রাখে এবং রেঞ্জ কোয়েরি দ্রুত সম্পন্ন করে।
2. **হ্যাশ টেবিল (Hash Map):** মেম্বারটির মান দিয়ে $O(1)$ সময়ে তার বর্তমান স্কোর এবং উপস্থিতি যাচাই করতে পারে।

### স্লাইডিং উইন্ডো লগের কাঠামো:
- **কী (Key):** `ratelimit:{scope}:{client_id}`
- **স্কোর (Score):** বর্তমান ফ্লোটিং-পয়েন্ট ইউনিক্স টাইমস্ট্যাম্প ($T$, যেমন: `1725883200.456789`)
- **মেম্বার (Member):** টাইমস্ট্যাম্প ও একটি ইউনিক ৮-অক্ষরের UUID হেক্স স্ট্রিং (`f"{current_time}:{uuid4().hex[:8]}"`)।  
  *মেম্বারে ইউনিক আইডি যুক্ত না করলে একই মাইক্রোসেকেন্ডে আসা দুটি ভিন্ন কনকারেন্ট রিকোয়েস্ট একই স্কোরে একটি মেম্বার হিসেবে ওভাররাইট হয়ে যেত এবং কোটা ভুল গণনা করত।*

### বিগ-ও (Big-O) জটিলতা বিশ্লেষণ:

| অপারেশন | কমান্ড | টাইম কমপ্লেক্সিটি | স্পেস কমপ্লেক্সিটি |
| :--- | :--- | :--- | :--- |
| ১. এক্সপায়ার্ড লগ ডিলিট | `ZREMRANGEBYSCORE key 0 (now - window)` | $\mathcal{O}(\log N + M)$ | $\mathcal{O}(1)$ |
| ২. সাময়িক লগ ইনসার্ট | `ZADD key score member` | $\mathcal{O}(\log N)$ | $\mathcal{O}(1)$ |
| ৩. সক্রিয় কাউন্ট গণনা | `ZCARD key` | $\mathcal{O}(1)$ | $\mathcal{O}(1)$ |
| ৪. প্রাচীনতম টাইমস্ট্যাম্প রিড | `ZRANGE key 0 0 WITHSCORES` | $\mathcal{O}(\log N)$ | $\mathcal{O}(1)$ |
| ৫. টিটিএল রিফ্রেশ | `EXPIRE key ttl` | $\mathcal{O}(1)$ | $\mathcal{O}(1)$ |
| **মোট নেটওয়ার্ক রাউন্ডট্রিপ** | **Single Atomic Redis Pipeline** | **$\mathcal{O}(\log N + M)$** | **$\mathcal{O}(N)$ per client** |

*(এখানে $N$ হলো স্লাইডিং উইন্ডোতে সক্রিয় রিকোয়েস্টের সংখ্যা, এবং $M$ হলো যে কয়টি পুরোনো রিকোয়েস্ট মুছে ফেলা হয়েছে।)*

---

## ৫. আর্কিটেকচারাল ফ্লো ডায়াগ্রাম

```
[ Incoming Client Request ]
           │
           ▼
[ RateLimitGuard (FastAPI Dependency) ]
  ├── 1. Client Identifier Resolve: X-API-Key -> X-Forwarded-For -> Host IP
  └── 2. RateLimiterService.check_distributed_rate_limit(key, limit, window)
           │
           ▼
┌─────────────────────── REDIS ATOMIC PIPELINE (MULTI/EXEC) ───────────────────────┐
│ 1. ZREMRANGEBYSCORE key 0 (now - window)   --> Prune logs older than window        │
│ 2. ZADD key now {now:uuid}                 --> Tentatively record request          │
│ 3. ZCARD key                              --> Get exact active count in window    │
│ 4. ZRANGE key 0 0 WITHSCORES              --> Inspect oldest active request time  │
│ 5. EXPIRE key (window + 2)                --> Refresh key memory auto-cleanup     │
└──────────────────────────────────────────────────────────────────────────────────┘
           │
           ▼
    [ current_count <= limit ? ]
          ├── YES ──► Return (False, count, 0.0)
          │           └── Execute Route Controller (HTTP 200 OK)
          │
          └── NO  ──► Rollback: ZREM key {now:uuid} (failed req consumes no quota)
                      Calculate Retry-After = (oldest_ts + window) - now
                      Raise HTTPException(429 Too Many Requests)
                      └── Inject RFC Headers:
                            Retry-After: 4
                            X-RateLimit-Limit: 5
                            X-RateLimit-Remaining: 0
```

---

## ৬. লাইন-বাই-লাইন কোড ব্যবচ্ছেদ

### ৬.১ `RateLimiterService` (`app/services/rate_limiter_service.py`)

```python
# অ্যাটমিক পাইপলাইনে ৫টি কমান্ড একসাথে এক্সিকিউট করা হয়
async with self._redis.pipeline(transaction=True) as pipe:
    # ১. চলমান উইন্ডোর বাইরে চলে যাওয়া সমস্ত পুরোনো টাইমস্ট্যাম্প মুছে ফেলা (ওপেন আপার বাউন্ড)
    pipe.zremrangebyscore(redis_key, 0, f"({current_time - window_seconds}")
    # ২. বর্তমান রিকোয়েস্টটি সাময়িকভাবে সেটে যোগ করা
    pipe.zadd(redis_key, {member: current_time})
    # ৩. সেটে মোট কয়টি উপাদান রয়েছে তা গণনা করা (O(1))
    pipe.zcard(redis_key)
    # ৪. সেটের সর্বনিম্ন স্কোরের (প্রাচীনতম) সদস্যের টাইমস্ট্যাম্প বের করা
    pipe.zrange(redis_key, 0, 0, withscores=True)
    # ৫. পুরো কী-টির উপর উইন্ডোর চেয়ে ২ সেকেন্ড বেশি TTL সেট করা যাতে নিষ্ক্রিয় কী মেমোরি নষ্ট না করে
    pipe.expire(redis_key, int(window_seconds) + 2)
    results = await pipe.execute()
```
**ব্যাখ্যা:** `transaction=True` নিশ্চিত করে যে মাল্টি-থ্রেডেড বা মাল্টি-পড পরিবেশে কোনো দুই রিকোয়েস্ট একে অপরের মাঝে ঢুকে রেস কন্ডিশন তৈরি করতে পারবে না।

```python
current_count = int(results[2])
oldest_entries = results[3]

if current_count <= limit:
    return False, current_count, 0.0

# লিমিট অতিক্রম করলে সাময়িক এন্ট্রি রোলব্যাক করা
await self._redis.zrem(redis_key, member)

oldest_ts = current_time
if oldest_entries and len(oldest_entries) > 0:
    oldest_ts = float(oldest_entries[0][1])

# ঠিক কতক্ষণ পর পুরোনো রিকোয়েস্টটি এক্সপায়ার হবে তা হিসাব করা
retry_after = max(0.0, (oldest_ts + window_seconds) - current_time)
return True, limit, retry_after
```
**ব্যাখ্যা:** যদি কোনো ক্লায়েন্টের রিকোয়েস্ট ব্লক হয়, তবে আমরা সাথে সাথে `zrem` চালিয়ে তার এন্ট্রিটি মুছে দিই। ফলে ব্লকড রিকোয়েস্ট ক্লায়েন্টের পরবর্তী বৈধ কোটা নষ্ট করে না।

### ৬.২ `RateLimitGuard` (`app/core/dependencies.py`)

```python
class RateLimitGuard:
    def __init__(self, limit: int, window_seconds: float, scope: str = "default") -> None:
        self.limit = limit
        self.window_seconds = window_seconds
        self.scope = scope

    async def __call__(
        self,
        request: Request,
        rate_limiter: RateLimiterService = Depends(get_rate_limiter_service),
    ) -> None:
        # ক্লায়েন্ট আইডেন্টিটি রিজলভেশন ক্যাসকেড
        client_id = request.headers.get("X-API-Key")
        if not client_id:
            forwarded_for = request.headers.get("X-Forwarded-For")
            client_id = forwarded_for.split(",")[0].strip() if forwarded_for else (request.client.host if request.client else "unknown")

        key = f"{self.scope}:{client_id}"
        is_limited, current_count, retry_after = await rate_limiter.check_distributed_rate_limit(
            key=key, limit=self.limit, window_seconds=self.window_seconds
        )

        if is_limited:
            retry_seconds = max(1, math.ceil(retry_after))
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Rate limit exceeded. Quota: {self.limit} requests per {self.window_seconds}s.",
                headers={
                    "Retry-After": str(retry_seconds),
                    "X-RateLimit-Limit": str(self.limit),
                    "X-RateLimit-Remaining": "0",
                },
            )
```
**ব্যাখ্যা:** `RateLimitGuard` একটি ডিপেন্ডেন্সি ইনজেকশন ক্লাস যা যেকোনো রুটের সাথে `Depends(RateLimitGuard(limit=5, window_seconds=10.0))` আকারে ডিক্ল্যারেটিভভাবে যুক্ত করা যায়।

---

## ৭. টেস্ট ড্রাইভেন ভেরিফিকেশন (TDD Suite)

আমরা `tests/test_redis_zset_rate_limiter.py`-তে ৫টি বাস্তবধর্মী টেস্ট কেস লিখে পুরো আর্কিটেকচার পুঙ্খানুপুঙ্খভাবে যাচাই করেছি:

1. **`test_continuous_rolling_expiration`:**  
   সিমুলেটেড টাইম এগিয়ে দিয়ে দেখা হয়েছে $T=100.0, 100.5, 101.0$-এ ৩টি রিকোয়েস্ট পাঠানোর পর $T=102.0$-এ ৪র্থ রিকোয়েস্ট প্রত্যাখ্যাত হয়। আবার $T=105.5$-এ পৌঁছালে ১ম রিকোয়েস্টটি এক্সপায়ার হয়ে জায়গা খালি করে এবং নতুন রিকোয়েস্ট সফলভাবে গৃহীত হয়।
2. **`test_boundary_burst_defect_defeat`:**  
   ফিক্সড উইন্ডোর বাউন্ডারি বার্স্ট ট্রিক প্রয়োগ করে $T=9.0$-এ ৩টি এবং $T=10.6$-এ আরও রিকোয়েস্ট পাঠিয়ে নিশ্চিত করা হয়েছে যে স্লাইডিং উইন্ডো কোনোভাবেই কোটার অতিরিক্ত ট্রাফিক ঢুকতে দেয় না।
3. **`test_concurrent_race_condition_defeat`:**  
   `asyncio.gather` দিয়ে ২০টি সমসাময়িক অ্যাসিনক্রোনাস রিকোয়েস্ট একসাথে পাঠানো হয় (যেখানে কোটা ছিল ১০)। অ্যাটমিক পাইপলাইনের কারণে ঠিক ১০টি রিকোয়েস্ট পাস করেছে এবং ঠিক ১০টি রিকোয়েস্ট ব্লক হয়েছে—কোনো ওভার-গ্রান্টিং ঘটেনি!
4. **`test_quota_enforcement_and_retry_after_headers`:**  
   HTTP ক্লায়েন্টের মাধ্যমে ৫টি রিকোয়েস্টে 200 OK এবং ৬ষ্ঠ রিকোয়েস্টে 429 Too Many Requests সহ `Retry-After`, `X-RateLimit-Limit`, `X-RateLimit-Remaining` হেডার পরীক্ষা করা হয়েছে।
5. **`test_live_rate_limit_metrics_endpoint`:**  
   লাইভ মেমোরি ইনস্পেকশন রুট `GET /metrics/rate-limit/{client_id}` দিয়ে সক্রিয় রিকোয়েস্ট ও কী-এর টিটিএল যাচাই করা হয়েছে।

---

## ৮. রুটের ব্যবহার ও কার্ল কমান্ড (cURL & HTTP Headers)

### ১. টেস্ট রেট লিমিট রুট কল করা:
```bash
curl -i -X GET "http://127.0.0.1:8000/test-rate-limit/distributed" \
     -H "X-API-Key: client_developer_pro"
```

**সফল রেসপন্স (HTTP 200 OK):**
```json
HTTP/1.1 200 OK
content-type: application/json

{
  "message": "Request accepted within distributed sliding window quota",
  "client_id": "client_developer_pro",
  "limit": 5,
  "window_seconds": 10.0
}
```

**কোটা শেষ হলে রেসপন্স (HTTP 429 Too Many Requests):**
```http
HTTP/1.1 429 Too Many Requests
Retry-After: 4
X-RateLimit-Limit: 5
X-RateLimit-Remaining: 0
content-type: application/json

{
  "detail": "Rate limit exceeded. Quota: 5 requests per 10.0s."
}
```

### ২. ক্লায়েন্টের লাইভ স্ট্যাটাস মনিটরিং:
```bash
curl -X GET "http://127.0.0.1:8000/metrics/rate-limit/test:client_developer_pro?window_seconds=10.0"
```

**রেসপন্স:**
```json
{
  "key": "ratelimit:test:client_developer_pro",
  "active_requests": 5,
  "ttl_seconds": 11,
  "window_seconds": 10.0,
  "oldest_timestamp": 1725883200.12,
  "newest_timestamp": 1725883206.54
}
```

---

## ৯. কমন প্রডাকশন মিস্টেকস ও গোল্ডেন রুলস

> [!CAUTION]
> **ভুল ১: নন-অ্যাটমিক চেক-দেন-অ্যাড (Non-Atomic Check-then-Add Race Condition)**  
> প্রথমে `zcard` দিয়ে চেক করা এবং তারপর পাইপলাইনের বাইরে আলাদা `zadd` চালানো সম্পূর্ণ ভুল। কনকারেন্ট ট্রাফিকের সময় হাজার হাজার রিকোয়েস্ট একসাথে `zcard < limit` সত্য পাবে এবং সবাই ডাটাবেসে ঢুকে পড়বে। সর্বদা পাইপলাইনের ভেতরে সাময়িক ইনসার্ট করে অ্যাটমিকভাবে কোটা যাচাই করুন।

> [!WARNING]
> **ভুল ২: মেম্বারে শুধু টাইমস্ট্যাম্প ব্যবহার করা (Collision on High TPS)**  
> যদি ZSET-এর মেম্বার হিসেবে শুধু `str(timestamp)` রাখা হয়, তবে এক মিলি-সেকেন্ডে দুটি রিকোয়েস্ট এলে Redis একই মেম্বারের স্কোর আপডেট করে দেবে। কাউন্টার বাড়বে না! সমাধান: সর্বদা মেম্বারে ইউনিক আইডি জুড়ুন (`f"{timestamp}:{uuid4().hex[:8]}"`)।

> [!TIP]
> **গোল্ডেন রুল: রিজেক্টেড রিকোয়েস্ট রোলব্যাক করা (Clean Rejected Entries)**  
> রিকোয়েস্ট লিমিট অতিক্রম করলে ZSET থেকে সেই রিকোয়েস্টের টোকেনটি `zrem` করে রোলব্যাক করতে হবে। নতুবা একজন অ্যাটাকার ব্লক খাওয়ার পরেও লাখ লাখ রিকোয়েস্ট পাঠিয়ে ZSET-এর মেমোরি ফুল করে দেবে এবং পরবর্তী বৈধ রিকোয়েস্টগুলোকেও অনন্তকাল ব্লক রাখবে।

---

## ১০. রোডম্যাপ ট্র্যাকিং ও পরবর্তী দিনের কানেকশন

- [x] **Day 36: Real-Time Leaderboard Service Architecture using Redis Sorted Sets (ZSET)**
- [x] **Day 37: Distributed Sliding Window Log Rate Limiter using Redis ZSET & Atomic Pipelines**
- [ ] **Day 38: Distributed Task Queue Architecture using Redis Streams & Consumer Groups**

**আগামীকাল Day 38-এ আমরা কী করব?**  
আমরা Redis-এর সবচেয়ে শক্তিশালী মেসেজিং ইঞ্জিন **Redis Streams** এবং **Consumer Groups** ব্যবহার করে একটি এন্টারপ্রাইজ গ্রেড ডিস্ট্রিবিউটেড ব্যাকগ্রাউন্ড টাস্ক কিউ তৈরি করব। সেল্যারির (Celery) ভারী ওভারহেড ছাড়াই কীভাবে মেসেজ অ্যাকনলেজমেন্ট (`XACK`), পেন্ডিং এন্ট্রি লিস্ট (`PEL`), এবং অটো-রিকভারি দিয়ে ফেইলড টাস্ক হ্যান্ডেল করতে হয়, তা আমরা শিখব!
