# Day 51: সেল্যারি ও রেডিস ব্রোকার সহযোগে ডিস্ট্রিবিউটেড অ্যাসিঙ্ক্রোনাস টাস্ক প্রসেসিং

## ১. আমরা কী বানিয়েছি? (What did we build?)
আজ ৫১তম দিনে আমরা এন্টারপ্রাইজ ব্যাকএন্ডের অত্যন্ত গুরুত্বপূর্ণ একটি আর্কিটেকচারাল মাইলফলক অর্জন করেছি—**সেল্যারি (Celery 5.6+) এবং রেডিস (Redis) মেসেজ ব্রোকারের সমন্বয়ে ডিস্ট্রিবিউটেড অ্যাসিঙ্ক্রোনাস টাস্ক কিউ (Distributed Asynchronous Task Queue)**।

যখন কোনো ক্লায়েন্ট বা ফ্রন্টএন্ড থেকে ভারী ও সময়সাপেক্ষ কোনো কাজের রিকোয়েস্ট আসে (যেমন: ৫–১০ সেকেন্ড সময় লাগা জটিল পিডিএফ/এক্সেল রিপোর্ট জেনারেশন কিংবা বাল্ক ট্রানজ্যাকশনাল ইমেইল পাঠানো), তখন সেই কাজ সরাসরি ফাস্টএপিআই-এর রিকোয়েস্ট-রেসপন্স সাইকেলে সম্পন্ন না করে, আমরা কাজটি রেডিস মেসেজ কিউতে পুশ করে দিয়ে ক্লায়েন্টকে চোখের পলকে (< ১০ মিলিসেকেন্ডে) **HTTP 202 Accepted** রেসপন্স এবং একটি অনন্য `task_id` ফেরত পাঠাই। ব্যাকগ্রাউন্ডে স্বাধীন সেল্যারি ওয়ার্কার প্রসেস সেই টাস্কটিকে তুলে নিয়ে প্রসেস করে এবং ফলাফল রেডিস রেজাল্ট ব্যাকএন্ডে সেভ করে রাখে। পরবর্তীতে ক্লায়েন্ট সেই `task_id` দিয়ে পোলিং এপিআই-এর মাধ্যমে কাজের বর্তমান স্ট্যাটাস (`PENDING`, `STARTED`, `SUCCESS`, `FAILURE`) এবং রেজাল্ট সংগ্রহ করতে পারে।

---

## 📌 ব্যবহৃত DSA ও আর্কিটেকচারাল প্যাটার্নের সুনির্দিষ্ট নাম
- **ডিস্ট্রিবিউটেড টাস্ক কিউ ও প্রোডিউসার-কনজিউমার প্যাটার্ন (Distributed Task Queue & Producer-Consumer Pattern via Celery & Redis Message Broker)**

---

## 🚀 প্রোডাকশনে ঠিক কখন ব্যবহার করব? (When to use in Production)
বাস্তব জীবনের প্রোডাকশন সিস্টেমে নিম্নলিখিত ৪টি সুনির্দিষ্ট ক্ষেত্রে এটি ব্যবহার বাধ্যতামূলক:
1. **ভারী পিডিএফ বা এক্সেল রিপোর্ট জেনারেশন:** ৫–১০ সেকেন্ড সময় লাগা ব্যালেন্স শিট, অডিট লগ বা অ্যানালিটিক্স রিপোর্ট ক্লায়েন্টকে ব্রাউজারে অপেক্ষায় না রেখে ব্যাকগ্রাউন্ডে তৈরি করা।
2. **ট্রানজ্যাকশনাল ইমেইল ও এসএমএস নোটিফিকেশন:** থার্ড-পার্টি ইমেইল গেটওয়ে (SendGrid/AWS SES) কিংবা এসএমএস গেটওয়ে সাময়িক স্লো বা ডাউন থাকলেও ইউজারের ব্রাউজার আটকে না রাখা এবং ব্যাকগ্রাউন্ডে অটোমেটিক রিট্রাই করা।
3. **ইমেজ প্রসেসিং ও ভিডিও এনকোডিং:** ইউজারের আপলোড করা ছবির থাম্বনেইল তৈরি, রেজোলিউশন রিসাইজ, ওয়াটারমার্ক বসানো বা ভিডিও কম্প্রেশন করার মতো ভারী সিপিইউ কাজ আলাদা সার্ভারে প্রসেস করা।
4. **ডিপ লার্নিং মডেল ইনফারেন্স বা বাল্ক ডেটা ইমপোর্ট:** ১ লক্ষ লাইনের CSV ফাইল পার্স করে ডেটাবেসে ইমপোর্ট করা কিংবা জটিল মেশিন লার্নিং মডেল ইনফারেন্স ব্যাকগ্রাউন্ডে ব্যাচ প্রসেস করা।

---

## 🎯 কী কারণে বা কোন পরিস্থিতিতে ব্যবহার করব? (Why to use / Technical Triggers)
- **FastAPI ইভেন্ট লুপ ফ্রিজিং চিরতরে বন্ধ করা:** সিপিইউ-ইন্টেনসিভ বা ধীরগতির ব্লকিং কাজ মূল এপিআই সার্ভার থেকে সম্পূর্ণ আলাদা ও বিচ্ছিন্ন প্রসেসে পাঠিয়ে এপিআই রেসপন্স টাইম < ২০ মিলিসেকেন্ডে নামিয়ে আনা।
- **অ্যাসিঙ্ক বাফারিং ও ব্যাকপ্রেশার হ্যান্ডলিং:** ফ্ল্যাশ সেল বা ঈদের দিন একসাথে ১০,০০০ ইউজার রিপোর্ট ডাউনলোড বা অর্ডার কনফার্মেশন চাইলেও সার্ভার ক্র্যাশ না করে রেডিস মেমোরি কিউতে টাস্ক জমা রাখে এবং ওয়ার্কার তার সক্ষমতা অনুযায়ী একটি একটি করে কাজ নামায়।
- **স্বয়ংক্রিয় রিট্রাই ও ফল্ট টলারেন্স:** এক্সটার্নাল কোনো এপিআই সাময়িক ডাউন থাকলে এক্সপোনেনশিয়াল ব্যাক-অফ (Exponential Backoff: ৫ সেকেন্ড, ১০ সেকেন্ড, ২০ সেকেন্ড...) নিয়মে ৩ বার স্বয়ংক্রিয় রিট্রাই নিশ্চিত করা।

---

## ২. বাস্তব জীবনের গল্প ও উপমা: ব্যস্ত হাসপাতালের ফ্রন্ট ডেস্ক বনাম পেছনের অপারেশন থিয়েটার ও ট্রলির উপমা
একটি অতি ব্যস্ত আধুনিক হাসপাতালের কথা চিন্তা করুন:
- **ভুল পদ্ধতি (Direct Synchronous Loop):** একজন রোগী ফ্রন্ট ডেস্কে এসে বলল, "আমার ওপেন হার্ট সার্জারি প্রয়োজন।" ফ্রন্ট ডেস্কের রিসেপশনিস্ট যদি বলে, "আচ্ছা আপনি ডেস্কেই বসুন, আমি এখনই ডেস্কের উপর আপনার হার্ট কেটে অপারেশন করে দিচ্ছি!"—তাহলে কি হবে? সেই ১টি অপারেশনে ৪ ঘণ্টা লাগবে, এবং পেছনের লাইনে দাঁড়িয়ে থাকা ৫০০ সাধারণ রোগী শুধু একটি প্যারাসিটামলের প্রেসক্রিপশন নেওয়ার জন্যও ৪ ঘণ্টা আটকে থেকে মারা যাবে। পুরো হাসপাতাল অচল হয়ে পড়বে।
- **সেল্যারি ও রেডিস ব্রোকার পদ্ধতি (Producer-Consumer Queue):**  
  1. রোগী ফ্রন্ট ডেস্কে আসামাত্র রিসেপশনিস্ট তাকে একটি টোকেন স্লিপ দিল: **"টোকেন নং: #৭৮৯২ (HTTP 202 Accepted Task ID)"** এবং রোগীকে বলল, "আপনার অপারেশনের ফাইল পেছনের অপারেশন থিয়েটারের ট্রলিতে (Redis Message Queue) পাঠিয়ে দেওয়া হয়েছে। আপনি ওয়েটিং রুমে বসুন।" এই পুরো প্রক্রিয়ায় লাগল মাত্র ৫ সেকেন্ড।
  2. ফ্রন্ট ডেস্ক মুক্ত হয়ে গেল এবং পেছনের শত শত সাধারণ রোগীকে সেবা দিতে লাগল।
  3. পেছনের বিশেষায়িত সার্জন ও নার্স দল (Celery Workers) ট্রলি থেকে সেই ফাইলটি তুলে নিয়ে অপারেশন শুরু করল এবং মনিটরে স্ট্যাটাস লিখে দিল: `STARTED`।
  4. অপারেশন সফলভাবে সম্পন্ন হলে তারা রেজাল্ট শিটটি রেকর্ডে জমা দিল: `SUCCESS`। রোগী তখন লাউঞ্জের স্ক্রিনে তার টোকেন নম্বর চেক করে ডিসচার্জ সার্টিফিকেট হাতে পেয়ে গেল।

---

## ৩. এটা না বানালে কী মহাবিপদ হতো? (The Production Disaster Without It)
১০ সেকেন্ডের মাত্র ১টি ভারী পিডিএফ তৈরি করতে গিয়ে এপিআই সার্ভারের থ্রেড বা ইভেন্ট লুপ ব্লক হয়ে যেত। পিক আওয়ারে যখন একসাথে ৫০ জন ইউজার রিপোর্ট জেনারেশনে ক্লিক করত, তখন ক্লাউডফ্লেয়ার বা এনজিনক্স (NGINX) থেকে **HTTP 504 Gateway Timeout** ধেয়ে আসত। ফলে সাধারণ লগইন, ব্যালেন্স চেক বা প্রোডাক্ট পেজও পুরো হ্যাং হয়ে যেত এবং ইউজাররা সার্ভারকে অফলাইন মনে করে সাইট ত্যাগ করত।

---

## ৪. আমাদের প্রজেক্টের আসল কোড ও কোডের লাইন-বাই-লাইন সহজ ব্যাখ্যা (Real Code & Detailed Line-by-Line Breakdown)

### ৪.১ সেল্যারি অ্যাপের প্রোডাকশন হার্ডেনিং কনফিগারেশন (`app/core/celery_app.py`)
```python
# app/core/celery_app.py
from celery import Celery
from app.core.config import get_settings

settings = get_settings()

celery_app = Celery(
    "fastapi_clean_architecture",
    broker=settings.redis_url,       # রেডিস কিউ যেখানে টাস্ক জমা থাকে
    backend=settings.redis_url,      # রেডিস ব্যাকএন্ড যেখানে রেজাল্ট সেভ হয়
    include=["app.tasks.report_tasks"],
)

# প্রোডাকশন হার্ডেনিং প্যারামিটার
celery_app.conf.update(
    task_serializer="json",          # সিকিউরিটি: পাইথন pickle বর্জন করে নিরাপদ JSON ব্যবহার
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,         # ওয়ার্কার কাজ শুরু করলেই STARTED স্ট্যাটাস এমিট করবে
    task_time_limit=300,             # হার্ড লিমিট: ৫ মিনিটের বেশি কোনো টাস্ক চললে কিল করে দেবে
    task_soft_time_limit=240,        # সফট লিমিট: ৪ মিনিটে ক্লিনআপের সুযোগ দিয়ে ওয়ার্নিং এক্সেপশন দেবে
    worker_prefetch_multiplier=1,    # ফেয়ার ডিসপ্যাচ: ওয়ার্কার একসাথে মাত্র ১টি কাজ প্রিফেচ করবে
    task_acks_late=True,             # ওয়ার্কার কাজ চলাকালীন ক্র্যাশ করলে টাস্কটি হারাবে না, আবার কিউতে যাবে
    result_expires=86400,            # রেজাল্ট ২৪ ঘণ্টা পর রেডিস মেমোরি থেকে এক্সপায়ার হবে
)
```
- **লাইন-বাই-লাইন ব্যাখ্যা:**
  - `broker=settings.redis_url`: এপিআই থেকে পাঠানো মেসেজগুলো রেডিসের লিস্ট বা স্ট্রিম কিউতে জমা হয়।
  - `task_serializer="json"`: পিকল (pickle) ডিসিরিয়ালাইজেশনে রিমোট কোড এক্সিকিউশন (RCE) অ্যাটাকের ভয় থাকে, তাই কঠোরভাবে নিরাপদ JSON ব্যবহার করা হয়েছে।
  - `worker_prefetch_multiplier=1`: ডিফল্টভাবে সেল্যারি ওয়ার্কার একসাথে ৪টি কাজ রিজার্ভ করে রাখে। ফলে একটি ওয়ার্কার বড় কাজ নিয়ে বসে থাকলে পেছনের ছোট কাজগুলো অনর্থক আটকে থাকে। এটি ১ করায় কাজের সমবণ্টন (Fair Scheduling) নিশ্চিত হয়।
  - `task_acks_late=True`: সাধারণ নিয়মে ওয়ার্কার কাজ হাতে নিয়েই অ্যাকনলেজ করে দেয়। ফলে কাজ চলাকালীন ওয়ার্কার ক্র্যাশ করলে টাস্কটি হারিয়ে যায়। কিন্তু এটি `True` থাকায় কাজ ১০০% শেষ হওয়ার পরই কেবল রেডিস থেকে টাস্ক ডিলিট হয়।

---

### ৪.২ আসল ব্যাকগ্রাউন্ড টাস্ক ও এক্সপোনেনশিয়াল ব্যাক-অফ রিট্রাই (`app/tasks/report_tasks.py`)
```python
# app/tasks/report_tasks.py
@celery_app.task(name="app.tasks.report_tasks.generate_pdf_report")
def generate_pdf_report(user_id: int, report_type: str) -> dict[str, Any]:
    """ভারী পিডিএফ রিপোর্ট জেনারেশন সিমুলেশন ও ক্রিপ্টোগ্রাফিক চেকসাম তৈরি।"""
    start_time = time.time()
    timestamp = datetime.now(UTC).isoformat()
    raw_content = f"PDF_REPORT_DATA|user_id={user_id}|type={report_type}|ts={timestamp}"
    checksum = hashlib.sha256(raw_content.encode("utf-8")).hexdigest()
    elapsed_seconds = round(time.time() - start_time, 4)

    return {
        "status": "COMPLETED",
        "user_id": user_id,
        "report_type": report_type,
        "filename": f"report_{user_id}_{report_type}_{int(time.time())}.pdf",
        "checksum_sha256": checksum,
        "size_bytes": 1024 * 42,
        "generated_at": timestamp,
        "execution_time_seconds": elapsed_seconds,
    }


@celery_app.task(
    bind=True,
    max_retries=3,
    default_retry_delay=5,
    name="app.tasks.report_tasks.send_transactional_email",
)
def send_transactional_email(
    self: Task,
    recipient: str,
    subject: str,
    template: str,
    context: dict[str, Any],
) -> dict[str, Any]:
    """স্বয়ংক্রিয় এক্সপোনেনশিয়াল ব্যাক-অফ রিট্রাই সমৃদ্ধ ইমেইল প্রেরণ টাস্ক।"""
    try:
        if context.get("simulate_error"):
            raise ConnectionError("Simulated SMTP gateway timeout error")

        return {
            "status": "SENT",
            "recipient": recipient,
            "subject": subject,
            "template": template,
            "delivered_at": datetime.now(UTC).isoformat(),
            "attempt": self.request.retries + 1,
        }
    except Exception as exc:
        if self.request.retries < self.max_retries:
            # ৫ সেকেন্ড, ১০ সেকেন্ড, ২০ সেকেন্ড... এক্সপোনেনশিয়াল বিলম্ব
            countdown = int(self.default_retry_delay * (2**self.request.retries))
            raise self.retry(exc=exc, countdown=countdown) from exc
        raise
```

---

### ৪.৩ সার্ভিস ও ট্রান্সপোর্ট রাউটিং লেয়ার (`app/services/task_service.py` & `app/routers/task_router.py`)
```python
# app/services/task_service.py
class TaskService:
    @staticmethod
    def dispatch_report_generation(user_id: int, report_type: str) -> str:
        # নন-ব্লকিং O(1) ডিসপ্যাচ
        async_result = generate_pdf_report.delay(user_id, report_type)
        return str(async_result.id)

    @staticmethod
    def get_task_status(task_id: str) -> dict[str, Any]:
        result = AsyncResult(task_id, app=celery_app)
        state = result.state
        if state == "SUCCESS":
            return {"task_id": task_id, "status": state, "result": result.result, "error": None}
        elif state == "FAILURE":
            return {"task_id": task_id, "status": state, "result": None, "error": str(result.result)}
        else:
            return {"task_id": task_id, "status": state, "result": None, "error": None}
```

```python
# app/routers/task_router.py
@router.post("/reports/generate", status_code=status.HTTP_202_ACCEPTED, response_model=TaskDispatchResponse)
def generate_report(request: ReportGenerateRequest) -> TaskDispatchResponse:
    task_id = TaskService.dispatch_report_generation(request.user_id, request.report_type)
    return TaskDispatchResponse(task_id=task_id, status="PENDING", message="Report generation task dispatched successfully.")

@router.get("/reports/status/{task_id}", response_model=TaskStatusResponse)
def get_report_status(task_id: str) -> TaskStatusResponse:
    status_data = TaskService.get_task_status(task_id=task_id)
    return TaskStatusResponse(**status_data)
```

---

## ৫. ডিএসএ ও টাস্ক কিউ মেকানিক্স (DSA & Internal Mechanics)
1. **FIFO মেসেজ বাফারিং (First-In, First-Out Queue):** রেডিসের মেমোরিতে টাস্কগুলো পুশ হয় $O(1)$ টাইম কমপ্লেক্সিটিতে (`RPUSH`), এবং ওয়ার্কাররা পপ করে $O(1)$ টাইমে (`BLPOP`)।
2. **নন-ব্লকিং $O(1)$ ডিসপ্যাচ:** এপিআই হ্যান্ডলার কোনো প্রসেসিং লজিক রান করে না; কেবল মেমোরিতে একটি ছোট JSON মেসেজ পুশ করে সাথে সাথে রেসপন্স ফেরত দেয়।
3. **অ্যাটমিক টাস্ক অ্যাকনলেজমেন্ট ও রেডিস মেমোরি হ্যাশ:** প্রতিটি টাস্কের স্টেট একটি রেডিস কিউতে সংরক্ষিত থাকে (`celery-task-meta-<uuid>`) যা $O(1)$ টাইমে লুকআপ করা যায়।

---

## ৬. ইন্টারভিউ ও ভাইভা প্রশ্ন (Interview Q&A)

### প্রশ্ন ১: FastAPI-এর নিজস্ব `BackgroundTasks` থাকতে আমরা কেন প্রোডাকশনে Celery ও Redis ব্যবহার করব?
**উত্তর:**  
FastAPI-এর বিল্ট-ইন `BackgroundTasks` একই পাইথন প্রসেসের মধ্যে রিকোয়েস্ট শেষ হওয়ার পর এক্সিকিউট হয়। এর ৩টি মারাত্মক সীমাবদ্ধতা রয়েছে:
1. **ক্র্যাশ ভালনারেবিলিটি:** কোনো ব্যাকগ্রাউন্ড কাজ চলাকালীন ইউভিকর্ন (Uvicorn) রিস্টার্ট বা সার্ভার ক্র্যাশ করলে সেই টাস্ক চিরতরে হারিয়ে যায় (কোনো পারসিস্টেন্স কিউ নেই)।
2. **সিপিইউ ব্লকিং:** ব্যাকগ্রাউন্ড টাস্ক যদি ভারী সিপিইউ কাজ (যেমন পিডিএফ জেনারেশন বা এনক্রিপশন) করে, তবে সেটি পাইথনের GIL-এর কারণে মূল ইভেন্ট লুপকেই স্লো করে দেয়।
3. **স্কেলিং সীমাবদ্ধতা:** `BackgroundTasks` অন্য কোনো পৃথক সার্ভারে ডিস্ট্রিবিউট করা যায় না।  
বিপরীতে, **Celery** সম্পূর্ণ আলাদা ওয়ার্কার প্রসেস বা ক্লাস্টারে চলে, কাজ রেডিস মেমোরিতে সুরক্ষিত রাখে, ক্র্যাশ করলে টাস্ক অটোমেটিক পুনরায় চালু করে (`task_acks_late=True`) এবং নোটিফিকেশনের জন্য অটো-রিট্রাই প্রদান করে।

### প্রশ্ন ২: Celery-তে `worker_prefetch_multiplier=1` এবং `task_acks_late=True` কেন এন্টারপ্রাইজ সিস্টেমের জন্য অত্যন্ত জরুরি?
**উত্তর:**  
- ডিফল্টভাবে সেল্যারির প্রিফেচ মাল্টিপ্লায়ার ৪ থাকে। যদি ৪টি ওয়ার্কার থাকে, তারা প্রত্যেকে ৪টি করে ১৬টি টাস্ক নিজেদের মেমোরিতে আগাম টেনে নিয়ে বসে থাকে। এখন কোনো ১টি ওয়ার্কার যদি ১০ মিনিটের বড় কাজ পায়, তার পেছনে আটকে থাকা বাকি ৩টি ছোট কাজ অন্য ফ্রি ওয়ার্কাররা নিতে পারে না (Worker Starvation)। `prefetch_multiplier=1` দিলে ওয়ার্কার কেবল ১টি কাজ শেষ করেই পরবর্তী কাজ টানবে, যা শতভাগ কাজের সুষম বণ্টন নিশ্চিত করে।
- ডিফল্টভাবে সেল্যারি টাস্ক পাওয়া মাত্রই অ্যাকনলেজ করে দেয়। ফলে কাজ শুরু করার পর যদি আউট-অব-মেমোরি (OOM) এররে ওয়ার্কার ক্র্যাশ করে, কাজটি চিরতরে হারিয়ে যায়। `task_acks_late=True` দিলে কাজ সফলভাবে শেষ হওয়ার পরই কেবল অ্যাকনলেজ পাঠানো হয়; ফলে প্রসেস ক্র্যাশ করলেও মেসেজ ব্রোকার টাস্কটিকে অন্য ওয়ার্কারের কাছে পুনরায় পাঠিয়ে দেয়।

---

## ৭. এক নজরে আসল মূল লজিক (Core Essence in 2–3 Lines)
> **FastAPI রিকোয়েস্ট হ্যান্ডলার কখনোই ভারী কাজ সরাসরি নিজে প্রসেস করবে না। হ্যান্ডলার কেবল রেডিস মেসেজ কিউতে টাস্কটি জমা দিয়ে ক্লায়েন্টকে সাথে সাথে HTTP 202 Accepted ও `task_id` ফেরত দেবে, আর ব্যাকগ্রাউন্ডে স্বাধীন Celery ওয়ার্কাররা সেই কাজ সম্পন্ন করে রেডিসে রেজাল্ট জমা রাখবে।**
