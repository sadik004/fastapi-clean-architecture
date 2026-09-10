# ডে ৭৮: গ্রেসফুল শাটডাউন ও SIGTERM কানেকশন ড্রেইনিং আর্কিটেকচার (Zero-Dropped Requests & Connection Draining)

---

### ১. আমরা কী বানিয়েছি? (What did we build?)
আমরা একটি এন্টারপ্রাইজ-গ্রেড ৩-ফেজ গ্রেসফুল শাটডাউন ও কানেকশন ড্রেইনিং (Graceful Shutdown & Connection Draining) আর্কিটেকচার তৈরি করেছি যা অপারেটিং সিস্টেমের টার্মিনেশন সিগন্যাল (`SIGTERM`, `SIGINT`) ইন্টারসেপ্ট করে, কুবারনেটিস রোলিং ডেপ্লয়মেন্টের সময় চলমান রিকোয়েস্ট ড্রপ হওয়া চিরতরে বন্ধ করে, কুবারনেটিস রেডিনেস প্রোবকে ট্রিপ করে ট্রাফিক বিচ্ছিন্ন করে এবং কোনো ট্রানজ্যাকশন না কেটে ডাটাবেস, ক্যাশ ও মেসেজ ব্রোকার কানেকশন পুল নিরাপদে ডিসপোজ করে। এতে রয়েছে:

1. **শাটডাউন ও লাইফসাইকেল স্টেট ম্যানেজার (`app/core/lifecycle.py`)**: থ্রেড-সেফ এবং অ্যাসিনক্রোনাস ইন-ফ্লাইট রিকোয়েস্ট ট্র্যাকার (`ShutdownManager`)।
2. **৩-ফেজ গ্রেসফুল শাটডাউন প্রোটোকল (The 3-Phase Graceful Shutdown Protocol)**:
   - **ফেজ ১: ট্রাফিক কাট-অফ (Trip Readiness Probe)**: ওএস থেকে `SIGTERM` আসার সাথে সাথে `is_shutting_down = True` সেট হয় এবং `/health/readiness` অবিলম্বে `HTTP 503 Service Unavailable` (`status: "unready"`) রিটার্ন করা শুরু করে, যার ফলে কুবারনেটিস ইনগ্রেস বা লোড ব্যালেন্সার ১-২ সেকেন্ডের মধ্যে এই কন্টেইনারকে রাউটিং টেবিল থেকে বাদ দিয়ে দেয়।
   - **ফেজ ২: ইন-ফ্লাইট কানেকশন ড্রেইনিং (In-Flight Request Draining)**: একটি নন-ব্লকিং অ্যাসিনক্রোনাস লুপ (`await asyncio.sleep(0.1)`) চালিয়ে সব চলমান রিকোয়েস্ট শূন্য (`in_flight_requests == 0`) হওয়া পর্যন্ত অপেক্ষা করা হয় (বা সর্বোচ্চ ৩০ সেকেন্ডের সেফটি টাইমআউট `SHUTDOWN_TIMEOUT = 30.0` পর্যন্ত)। চলমান রিকোয়েস্টগুলোর রেসপন্সে `Connection: close` হেডার ইনজেক্ট করা হয় যাতে ক্লায়েন্ট বা ব্রাউজার তাদের পারসিস্টেন্ট TCP keep-alive সকেট বন্ধ করে দেয়।
   - **ফেজ ৩: স্টেট ও রিসোর্স ডিসপোজাল (Clean Exit)**: সব রিকোয়েস্ট সফলভাবে শেষ হওয়ার পর কাফকা প্রডিউসার ফ্লাশ করা (`await close_kafka_producer()`), র্যাবিটএমকিউ বন্ধ করা, রেডিস পুল ক্লোজ করা এবং প্রাইমারি ও রেপ্লিকা পোস্টগ্রেস ইঞ্জিন ডিসপোজ করা (`await primary_engine.dispose()`, `await replica_engine.dispose()`)।
3. **মিডলওয়্যার লাইফসাইকেল ইন্সট্রুমেন্টেশন (`app/core/middleware.py`)**: প্রতিটি রিকোয়েস্টের শুরুতে কাউন্টার বৃদ্ধি (`increment_in_flight`), শেষে গ্যারান্টিড `finally` ব্লকে কাউন্টার হ্রাস (`decrement_in_flight`), এবং শাটডাউন চলাকালীন `Connection: close` হেডার ইনজেকশন।
4. **রেডিনেস প্রোব গেটকিপিং ও ডায়াগনস্টিক এপিআই (`app/routers/health_router.py`)**: শাটডাউনের সময় সরাসরি ৫০৩ হ্যান্ডলিং এবং লাইভ মনিটরিংয়ের জন্য `GET /health/shutdown-status` এন্ডপয়েন্ট।
5. **ক্রস-প্ল্যাটফর্ম সিগন্যাল রেজিস্ট্রেশন**: উইন্ডোজ (ProactorEventLoop) এবং লিনাক্স (asyncio loop signal handlers) উভয় প্ল্যাটফর্মেই ক্র্যাশ-ফ্রি সিগন্যাল হ্যান্ডলিং।

---

### ২. 📌 ব্যবহৃত DSA ও প্রসেস লাইফসাইকেল প্যাটার্নের নাম
- **গ্রেসফুল শাটডাউন ও সিগটার্ম কানেকশন ড্রেইনিং (Graceful Shutdown & SIGTERM Lifecycle — POSIX Signals, In-Flight Request Draining & Asyncio Resource Cleanup)**
- **অ্যাটমিক কাউন্টার ও ইন-ফ্লাইট ট্র্যাকিং (Thread-Safe Atomic Request In-Flight Accounting — $\mathcal{O}(1)$ Increment/Decrement)**
- **অ্যাসিনক্রোনাস নন-ব্লকিং পোলিং ড্রেইনিং লুপ (Non-Blocking Asyncio Event Loop Drain Barrier via `await asyncio.sleep(0.1)`)**
- **সার্কিট ট্রিপিং রেডিনেস প্রোব প্যাটার্ন (Readiness Probe Gatekeeping & Immediate Upstream Traffic Shedding)**
- **কানেকশন কিপ-অ্যালাইভ টিয়ার-ডাউন প্যাটার্ন (HTTP/1.1 `Connection: close` Socket Termination)**

---

### ৩. 🚀 প্রোডাকশনে ঠিক কখন ব্যবহার করব? (When to use in Production)
- **কুবারনেটিস রোলিং ডেপ্লয়মেন্ট (Rolling Updates):** নতুন কোড ডেপ্লয় করার সময় পুরোনো কন্টেইনার যেন কাস্টমার ড্রপ না করে মরে। নতুন পড রেডি হওয়ার পর কুবারনেটিস পুরোনো পডকে `SIGTERM` পাঠায়; আমাদের ড্রেইনিং আর্কিটেকচার নিশ্চিত করে যে প্রসেসিংয়ে থাকা সমস্ত রিকোয়েস্ট সম্পূর্ণ না হওয়া পর্যন্ত পুরোনো পডটি জীবিত থাকবে।
- **ক্লাউড অটো-স্কেলিং ডাউনসাইজিং (PreStop Lifecycle Hook):** পিক আওয়ার পার হয়ে গেলে ট্রাফিক কমে যায় এবং কুবারনেটিস HPA (Horizontal Pod Autoscaler) অতিরিক্ত সার্ভার/কন্টেইনার বন্ধ করে দেয়। ডাউনস্কেলিংয়ের সময় যেন কোনো ইউজারের পেমেন্ট বা অর্ডার মাঝপথে হারিয়ে না যায়।
- **সার্ভার রিবুট ও নোড মেইনটেন্যান্স:** ক্লাউড প্রোভাইডারের নোড আপগ্রেড, কার্নেল প্যাচিং বা সার্ভার মেইনটেন্যান্সের সময় নোড ড্রেন (cordon & drain) করা হয়। মেইনটেন্যান্স চলাকালীন দীর্ঘমেয়াদী পেমেন্ট ট্রানজ্যাকশন অক্ষত রেখে নিরাপদে সার্ভিস বন্ধ করতে।

---

### ৪. 🎯 কী কারণে বা কোন পরিস্থিতিতে ব্যবহার করব? (Why to use / Technical Triggers)
- **৫০২ ব্যাড গেটওয়ে (502 Bad Gateway) চিরতরে ধ্বংস করা:** কন্টেইনার রিস্টার্টের সময় মাঝপথের রিকোয়েস্ট ড্রপ হওয়া বন্ধ করা। সাধারণ আর্কিটেকচারে কন্টেইনার মরার সময় ক্লায়েন্ট `502 Bad Gateway` অথবা `Connection Reset by Peer` এরর দেখতে পায়।
- **ডাটাবেস কানেকশন লিক ও ডাটা করাপশন রোধ:** অর্ধেক চলা ডাটাবেস ট্রানজ্যাকশন ড্রপ না হয়ে নিরাপদে `commit` বা `rollback` নিশ্চিত করা। ডাটাবেস ইঞ্জিন ডিসপোজ করার আগে সব ট্রানজ্যাকশন সম্পূর্ণ হয়।
- **মেমোরি বাফার ও কাফকা ফ্লাশিং:** অ্যাপ্লিকেশনের লোকাল মেমোরিতে জমে থাকা ডোমেন ইভেন্ট ও অডিট লগ ব্যাচ মেসেজ ব্রোকারে (`await producer.flush()`) সফলভাবে পুশ হওয়া নিশ্চিত করা।

---

### ৫. বাস্তব জীবনের গল্প ও উপমা (Bank Closing Time Analogy)
একটি ঐতিহ্যবাহী ব্যাংকের সান্ধ্যকালীন কার্যক্রমের কথা চিন্তা করুন:

বিকাল ৪টা বাজলে ব্যাংকের প্রধান ফটকের দারোয়ান ফটক বন্ধ করে দেয় এবং বাইরে "আজকের মতো লেনদেন সমাপ্ত" নোটিশ ঝুলিয়ে দেয়। 
- **খারাপ ও আনাড়ি ব্যাংক ম্যানেজার (হঠাৎ প্রসেস কিল / SIGKILL):** ৪টা বাজার সাথে সাথে ম্যানেজার মেইন বিদ্যুৎ সুইচ বন্ধ করে দিল, ক্যাশ কাউন্টারের ড্রয়ার লক করে দিল এবং লাইনে দাঁড়িয়ে থাকা গ্রাহকদের ঘাড় ধাক্কা দিয়ে রাস্তায় বের করে দিল! ফলাফল: যে গ্রাহকের অর্ধেক টাকা গোনা হয়েছিল তার টাকা হারিয়ে গেল, ব্যালেন্স গরমিল হলো এবং ব্যাংকের বিরুদ্ধে মামলা হলো।
- **পেশাদার ও দক্ষ ব্যাংক ম্যানেজার (গ্রেসফুল শাটডাউন ও ড্রেইনিং):**
  1. **ফেজ ১ (ফটক বন্ধ / রেডিনেস প্রোব ট্রিপ):** ৪টা বাজার সাথে সাথে দারোয়ান নতুন কোনো গ্রাহককে ব্যাংকে ঢুকতে নিষেধ করল (`/health/readiness` returns 503)।
  2. **ফেজ ২ (ভেতরের গ্রাহকদের সেবা সমাপ্ত / ড্রেইনিং):** ব্যাংকের ভেতরে ইতোমধ্যে যে ২০ জন গ্রাহক ক্যাশ কাউন্টারে লাইনে দাঁড়িয়ে আছেন, তাদের সবাইকে শান্তভাবে টাকা জমা দেওয়া ও উত্তোলনের সুযোগ দেওয়া হলো (`in_flight_requests` শূন্যে নেমে আসা)। কাউন্টার থেকে ক্যাশিয়ার প্রতিটি গ্রাহককে রসিদ দিয়ে বলে দিল, "আজকে আপনার কাজ শেষ, কাল আবার আসবেন" (`Connection: close`)।
  3. **ফেজ ৩ (ভল্ট ও খাতা বন্ধ / ক্লিন এক্সিট):** শেষ গ্রাহকটি বের হয়ে যাওয়ার পর ক্যাশিয়াররা হিসাব মেলাল, টাকা ভল্টে রেখে তালা দিল (`producer.flush()`), লেজার বই সিল করল এবং কম্পিউটার বন্ধ করে আলো নিভিয়ে ব্যাংক লক করল (`engine.dispose()`, `redis.close()`)।

আমাদের `ShutdownManager` হলো এই দক্ষ ও বিচক্ষণ ব্যাংক ম্যানেজার!

---

### ৬. এটা না বানালে কী মহাবিপদ হতো? (The Disaster Scenario)
ধরা যাক, আমরা প্রতিদিন প্রোডাকশনে ৭-৮ বার নতুন ফিচার ডেপ্লয় করি। পিক আওয়ারে প্রতি সেকেন্ডে ১,২০০টি রিকোয়েস্ট প্রসেস হচ্ছে।

কুবারনেটিস যখন আমাদের পডকে নতুন ভার্সনে আপডেট করতে যাবে, সে পুরোনো পডে `SIGTERM` পাঠাবে। যদি গ্রেসফুল শাটডাউন ও ড্রেইনিং না থাকে:
1. ইউভিকর্ন বা পাইথন প্রসেস তাৎক্ষণিকভাবে প্রসেস বন্ধ করে এক্সিট করবে।
2. মাঝপথে প্রসেসিংয়ে থাকা ১৫০টি পেমেন্ট ট্রানজ্যাকশনের ডাটাবেস সকেট ছিঁড়ে যাবে। কাস্টমারের ব্যাংক অ্যাকাউন্ট থেকে টাকা কেটে নেওয়া হবে, কিন্তু আমাদের ডাটাবেসে অর্ডারের স্ট্যাটাস আপডেট হবে না!
3. ব্রাউজারে ইউজাররা লাল রঙের কুৎসিত `502 Bad Gateway` দেখতে পাবে।
4. মেমোরিতে জমে থাকা কাফকা ইভেন্ট ব্যাচ ব্রোকারে পৌঁছানোর আগেই মেমোরি থেকে মুছে যাবে, ফলে অ্যাকাউন্ট ব্যালেন্স ও নোটিফিকেশনে স্থায়ী ডাটা ইনকনসিস্টেন্সি তৈরি হবে।
5. কোম্পানির এসএলএ (SLA) ভায়োলেশনের কারণে হাজার হাজার ডলার জরিমানা দিতে হবে।

---

### ৭. আমাদের প্রজেক্টের আসল কোড ও কোডের লাইন-বাই-লাইন সহজ ব্যাখ্যা

#### (ক) শাটডাউন ম্যানেজার আর্কিটেকচার (`app/core/lifecycle.py`)
```python
class ShutdownManager:
    """Enterprise-grade 3-phase graceful shutdown and connection draining manager."""

    def __init__(self, shutdown_timeout: float = 30.0) -> None:
        self.is_shutting_down: bool = False
        self.in_flight_requests: int = 0
        self.shutdown_timeout: float = shutdown_timeout
        self._drain_event: asyncio.Event = asyncio.Event()

    def increment_in_flight(self) -> None:
        """Increment active in-flight request counter (O(1))."""
        self.in_flight_requests += 1

    def decrement_in_flight(self) -> None:
        """Decrement active in-flight request counter with non-negative safety (O(1))."""
        self.in_flight_requests = max(0, self.in_flight_requests - 1)
        if self.in_flight_requests == 0 and self.is_shutting_down:
            self._drain_event.set()

    def initiate_shutdown(self) -> None:
        """Trip Phase 1: Cut off new traffic by setting is_shutting_down = True."""
        if not self.is_shutting_down:
            self.is_shutting_down = True
            logger.info(
                "graceful_shutdown_initiated",
                phase="phase_1_traffic_cutoff",
                in_flight_requests=self.in_flight_requests,
                message="Readiness probe tripped to HTTP 503. Awaiting connection draining.",
            )
            if self.in_flight_requests == 0:
                self._drain_event.set()

    async def wait_for_drain(self, timeout: float | None = None) -> bool:
        """Trip Phase 2: Asynchronously await draining of in-flight requests.
        
        Uses non-blocking asyncio polling loop yielding via await asyncio.sleep(0.1).
        """
        effective_timeout = timeout if timeout is not None else self.shutdown_timeout
        self.initiate_shutdown()

        if self.in_flight_requests == 0:
            return True

        loop = asyncio.get_running_loop()
        start_time = loop.time()
        while self.in_flight_requests > 0:
            elapsed = loop.time() - start_time
            if elapsed >= effective_timeout:
                logger.warning("graceful_shutdown_drain_timeout_exceeded", remaining=self.in_flight_requests)
                return False
            sleep_duration = min(0.1, max(0.001, effective_timeout - elapsed))
            await asyncio.sleep(sleep_duration)

        return True
```
- **ব্যাখ্যা:** 
  - `increment_in_flight` এবং `decrement_in_flight` ফাংশন দুটি $\mathcal{O}(1)$ সময় জটিলতায় রিকোয়েস্ট সংখ্যা ট্র্যাকিং করে। `max(0, ...)` ব্যবহারের ফলে কোনো অনাকাঙ্ক্ষিত পরিস্থিতিতে কাউন্টার কখনো ঋণাত্মক (নেগেটিভ) হতে পারে না।
  - `initiate_shutdown` ফ্ল্যাগ পরিবর্তন করে ফেজ ১ কার্যকর করে।
  - `wait_for_drain` ফাংশনে `await asyncio.sleep(sleep_duration)` ব্যবহার করে ইভেন্ট লুপের নিয়ন্ত্রণ অন্যান্য রানিং কো-রুটিনকে ছেড়ে দেওয়া হয়, যার ফলে ব্যাকগ্রাউন্ড ডাটাবেস কোয়েরি ও পেমেন্ট রিকোয়েস্টগুলো কোনো ল্যাগ বা ব্লকিং ছাড়াই সম্পন্ন হতে পারে।

#### (খ) মিডলওয়্যারে রিকোয়েস্ট কাউন্টিং ও হেডার ইনজেকশন (`app/core/middleware.py`)
```python
# ১. রিকোয়েস্ট প্রবেশের সাথে সাথে কাউন্টার বৃদ্ধি
shutdown_mgr = get_shutdown_manager()
shutdown_mgr.increment_in_flight()

try:
    response = await call_next(request)
    
    # ২. শাটডাউন চলাকালীন রেসপন্সে Connection: close হেডার ইনজেক্ট করা
    if shutdown_mgr.is_shutting_down:
        response.headers["Connection"] = "close"
        
    return response
finally:
    # ৩. রিকোয়েস্ট সফল হোক বা এরর হোক, গ্যারান্টিড কাউন্টার হ্রাস
    shutdown_mgr.decrement_in_flight()
```
- **ব্যাখ্যা:** `finally` ব্লকে `decrement_in_flight()` রাখা বাধ্যতামূলক; যদি কোনো এন্ডপয়েন্টে আনহ্যান্ডলড ক্র্যাশ বা ৫০০ এররও ঘটে, তবুও কাউন্টার একুরেট থাকবে এবং মেমোরিতে কখনো লিক হবে না। `Connection: close` হেডারটি ক্লায়েন্টের ব্রাউজার ও ইনগ্রেস প্রক্সিকে নির্দেশ দেয় যে পরবর্তী রিকোয়েস্টের জন্য এই TCP সকেট যেন রি-ইউজ না করা হয়।

#### (গ) রেডিনেস প্রোব গেটকিপিং (`app/routers/health_router.py`)
```python
@router.get("/readiness")
async def readiness_probe(response: Response, ...) -> dict[str, Any]:
    shutdown_mgr = get_shutdown_manager()
    if shutdown_mgr.is_shutting_down:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {
            "status": "unready",
            "reason": "Server is draining in-flight connections for graceful shutdown",
            "in_flight_requests": shutdown_mgr.in_flight_requests,
        }
    ...
```
- **ব্যাখ্যা:** শাটডাউন শুরু হওয়ার সাথে সাথে কোনো ডাটাবেস বা ক্যাশ পিং না করেই অবিলম্বে HTTP 503 রিটার্ন করা হয়। কুবারনেটিস প্রতি ১ সেকেন্ডে এই প্রোব চেক করে এবং সাথে সাথে পডের আইপি ইনগ্রেস এন্ডপয়েন্ট পুল থেকে মুছে ফেলে।

#### (ঘ) লাইফস্প্যান ফেজ ৩ ক্লিন এক্সিট (`app/main.py`)
```python
@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    # স্টার্টআপ কোড...
    shutdown_mgr = get_shutdown_manager()
    shutdown_mgr.register_signal_handlers()

    yield
    # ফেজ ২: সমস্ত ইন-ফ্লাইট রিকোয়েস্ট শেষ হওয়ার জন্য অপেক্ষা
    await shutdown_mgr.wait_for_drain()

    # ফেজ ৩: রিসোর্স ও কানেকশন পুল ডিসপোজাল
    shutdown_tracer()
    await close_kafka_producer()
    await close_rabbitmq()
    await close_redis_pool()
    await primary_engine.dispose()
    await replica_engine.dispose()
```

---

### ৮. ডিএসএ ও সিগন্যাল মেকানিক্স (DSA & Signal Mechanics)
- **POSIX Signal Handling (`signal.SIGTERM`, `signal.SIGINT`):** কুবারনেটিস যখন কোনো কন্টেইনার ডিলিট করতে চায়, সে প্রসেসের রুট PID-তে `SIGTERM` (Signal 15) পাঠায়। আমরা লিনাক্সে `loop.add_signal_handler(signal.SIGTERM, callback)` ব্যবহার করে এই সিগন্যাল ফাঁদে ফেলি। উইন্ডোজ প্ল্যাটফর্মে ProactorEventLoop-এর সীমাবদ্ধতা বিবেচনা করে আমরা সেফ ফলব্যাক হ্যান্ডলার নিবন্ধন করি।
- **Asyncio Event Drain Loop ($\mathcal{O}(1)$ Non-blocking Sleep):** ইন-ফ্লাইট ড্রেইনিং লুপে যদি আমরা সিঙ্ক্রোনাস `time.sleep(0.1)` ব্যবহার করতাম, তবে পাইথনের মূল ইভেন্ট লুপ ফ্রিজ হয়ে যেত এবং কোনো রিকোয়েস্ট কখনোই শেষ হতে পারত না (Deadlock Disaster)! আমরা ব্যবহার করেছি `await asyncio.sleep(0.1)`, যা কো-রুটিনকে সাসপেন্ড করে অন্য সমস্ত রিকোয়েস্টের I/O সম্পন্ন করার সুযোগ দেয়।
- **Atomic Counters ($\mathcal{O}(1)$ Time Complexity):** পাইথনে গ্লোবাল ইন্টারপ্রেটার লক (GIL) থাকার কারণে সাধারণ পূর্ণসংখ্যা বৃদ্ধি (`+= 1`) এবং হ্রাস (`-= 1`) একক থ্রেডের অ্যাসিনক্রোনাস কোডে রেস-কন্ডিশন মুক্ত এবং মেমোরিতে $\mathcal{O}(1)$ অপারেশনে ঘটে।
- **Clean Process Exit Code 0:** সব কানেকশন বন্ধ হওয়ার পর লাইফস্প্যান জেনারেটর স্বাভাবিকভাবে এক্সিট করে, যার ফলে প্রসেস অপারেটিং সিস্টেমকে রিটার্ন কোড `0` প্রদান করে।

---

### ৯. ইন্টারভিউ ও ভাইভা প্রশ্ন

**প্রশ্ন ১: `SIGTERM` বনাম `SIGKILL`-এর পার্থক্য কী এবং কুবারনেটিসে কেন সরাসরি `SIGKILL` ব্যবহার করা মারাত্মক ক্ষতিকর?**  
**উত্তর:**  
- **`SIGTERM` (Signal 15):** এটি হলো একটি মার্জিত ও বিনম্র অনুরোধ (Termination Request)। প্রসেস এই সিগন্যালটি ইন্টারসেপ্ট (catch) করতে পারে, চলমান কাজ শেষ করতে পারে, ডাটাবেস কানেকশন পুল ক্লোজ করতে পারে এবং মেমোরি বাফার ডিস্কে লিখে স্বাভাবিকভাবে এক্সিট করতে পারে।
- **`SIGKILL` (Signal 9):** এটি হলো ওএস কার্নেলের সরাসরি মৃত্যুদণ্ড (Uncatchable Force Kill)। প্রসেস এই সিগন্যাল ইন্টারসেপ্ট বা হ্যান্ডেল করতে পারে না; কার্নেল সাথে সাথে প্রসেসের মেমোরি পেজ মুছে দেয়।
- **কুবারনেটিসে ক্ষতিকর কেন:** কুবারনেটিসে সরাসরি `SIGKILL` দিলে বা `SIGTERM` ইগনোর করলে মাঝপথে থাকা সমস্ত পেমেন্ট ও ডাটাবেস ট্রানজ্যাকশন সকেট আকস্মিকভাবে কেটে যায়। ফলে কাস্টমাররা `502 Bad Gateway` পায়, ডাটাবেস লক আটকে থাকে এবং ডাটা করাপশন ঘটে। তাই কুবারনেটিস প্রথমে `SIGTERM` পাঠায় এবং ডিফল্ট ৩০ সেকেন্ড গ্রেস পিরিয়ড দেয়; যদি এই সময়ের মধ্যে প্রসেস গ্রেসফুলি না মরে, তবেই কেবল বাধ্য হয়ে `SIGKILL` পাঠায়।

**প্রশ্ন ২: কীভাবে কুবারনেটিসে Zero-Downtime Deployment নিশ্চিত করবেন এবং সেখানে Readiness Probe ও PreStop Hook-এর ভূমিকা কী?**  
**উত্তর:**  
জিরো-ডাউনটাইম ডেপ্লয়মেন্টের জন্য ৩টি স্তরের সমন্বয় প্রয়োজন:
1. **রেডিনেস প্রোব ট্রিপিং:** `SIGTERM` পাওয়ার সাথে সাথে `/health/readiness` প্রোবকে HTTP 503 রিটার্ন করানো, যাতে কুবারনেটিস এন্ডপয়েন্ট কন্ট্রোলার ১-২ সেকেন্ডে এই পডটিকে ইনগ্রেসের আপস্ট্রিম রাউটিং থেকে সরিয়ে দেয়।
2. **কানেকশন ড্রেইনিং ও `Connection: close`:** চলমান সমস্ত রিকোয়েস্ট শেষ না হওয়া পর্যন্ত প্রসেসকে জীবিত রাখা এবং রেসপন্সে `Connection: close` পাঠিয়ে ক্লায়েন্টের keep-alive সকেট বন্ধ করা।
3. **PreStop Hook ও Grace Period:** কুবারনেটিসের ইনগ্রেস কনফিগারেশন প্রপাগেশনে ১-২ সেকেন্ড সময় লাগতে পারে। তাই পড স্পেসিফিকেশনে `lifecycle.preStop.exec.command: ["sleep", "5"]` এবং `terminationGracePeriodSeconds: 60` কনফিগার করা হয়, যাতে ইনগ্রেস পুরোপুরি ট্রাফিক সরানো পর্যন্ত অ্যাপ্লিকেশন জীবিত থাকে এবং কোনো রিকোয়েস্ট ড্রপ না খায়।

---

### ১০. এক নজরে আসল মূল লজিক (২–৩ লাইনে মূল সারমর্ম)
১. `SIGTERM` সিগন্যাল পেলেই সাথে সাথে রেডিনেস প্রোব ট্রিপ (HTTP 503) করে ইনগ্রেস ট্রাফিক বিচ্ছিন্ন করতে হবে।  
২. নন-ব্লকিং অ্যাসিনক্রোনাস লুপে (`await asyncio.sleep(0.1)`) ইন-ফ্লাইট রিকোয়েস্ট শূন্য না হওয়া পর্যন্ত অপেক্ষা করতে হবে এবং রেসপন্সে `Connection: close` হেডার দিতে হবে।  
৩. সমস্ত ইন-ফ্লাইট ট্রানজ্যাকশন শতভাগ সফলভাবে শেষ হওয়ার পরই কেবল কাফকা, রেডিস এবং ডাটাবেস পুল ক্লোজ করে এক্সিট করতে হবে।
