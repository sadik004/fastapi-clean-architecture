# ডে ১৪: গ্লোবাল এক্সেপশন হ্যান্ডলিং (ডোমেইন এক্সেপশন হায়ারার্কি, সেন্ট্রালাইজড এরর এনভেলাপ ও নিরাপদ 500 মাস্কিং)

> **তারিখ**: ২০২৬-০৯-০৮  
> **ভূমিকা**: জুনিয়র শিক্ষানবিস ব্যাকএন্ড ইঞ্জিনিয়ার  
> **মেন্টর ও লিড আর্কিটেক্ট**: ইউজার  
> **মূল দর্শন**: রাউটার ও বিজনেস সার্ভিস থাকবে সম্পূর্ণ রোগমুক্ত ও ডিকুপল্ড—ডোমেইন লেয়ারে ছুড়ুন খাঁটি ডোমেইন এক্সেপশন, আর সেন্ট্রাল হ্যান্ডলারে সেগুলোকে রূপান্তর করুন স্ট্যান্ডার্ডাইজড এরর এনভেলাপে, যাতে ৫০০ ইন্টারনাল স্ট্যাকট্রেসের এক ফোঁটাও ক্লায়েন্টের চোখে না পড়ে।

---

## ১. আমরা কী বানিয়েছি? (What did we build?)

আজকের সেশনে আমরা আমাদের পুরো অ্যাপ্লিকেশন জুড়ে একটি সুশৃঙ্খল, নিরাপদ এবং এন্টারপ্রাইজ-গ্রেড এরর হ্যান্ডলিং সিস্টেম প্রতিষ্ঠা করেছি:
1. **ইউনিফাইড এন্টারপ্রাইজ এরর এনভেলাপ (`app/schemas/error.py`)**: প্রতিটি ব্যর্থ রিকোয়েস্টের জন্য (400, 401, 403, 404, 409, 422, 500) একটি সুনির্দিষ্ট, মেশিন-রিডেবল ও মানব-পাঠযোগ্য ফরম্যাট তৈরি করা হয়েছে (`code`, `message`, `status_code`, `timestamp`, `trace_id`, `details`)।
2. **ডিকুপল্ড ডোমেইন এক্সেপশন হায়ারার্কি (`app/core/exceptions.py`)**: সার্ভিস এবং রিপোজিটরি লেয়ার থেকে সমস্ত `HTTPException` সম্পূর্ণ মুছে ফেলে বিশুদ্ধ পাইথন ডোমেইন এক্সেপশন শ্রেণি তৈরি করা হয়েছে (`EntityNotFoundException`, `EntityConflictException`, ইত্যাদি)।
3. **সেন্ট্রালাইজড এক্সেপশন হ্যান্ডলার রেজিস্ট্রি (`app/core/exception_handlers.py`)**: রাউটারের ভেতর শত শত ক্লান্তিকর `try/except` ব্লক দূর করে গ্লোবাল হ্যান্ডলারের মাধ্যমে $\mathcal{O}(1)$ টাইমে এরর রেসপন্স জেনারেশন নিশ্চিত করা হয়েছে।
4. **জিরো ইনফরমেশন লিকেজ (Zero Leakage 500 Masking)**: অপ্রত্যাশিত কোনো বাগ বা সার্ভার ক্র্যাশের ক্ষেত্রে পাইথনের অভ্যন্তরীণ ফাইল পাথ বা ডেটাবেজ স্ট্যাকট্রেস ক্লায়েন্টকে না দেখিয়ে একটি সুরক্ষিত জেনেরিক মেসেজ এবং ট্র্যাক করার জন্য ইউনিক `trace_id` (UUIDv4) প্রদান করা।

---

## ২. বাস্তব জীবনের গল্প ও উপমা (Real-World Analogy)

### আধুনিক হাসপাতালের ট্রায়াজ ডক্টর ও সেন্ট্রাল প্রেসক্রিপশন টিকিট

কল্পনা করুন আপনি একটি আধুনিক আন্তর্জাতিক হাসপাতালে গেছেন:

#### ভুল ক্যাওটিক পদ্ধতি (Unsanitized Stack Trace):
- একজন রোগী পেটে ব্যথা নিয়ে এসেছে। জুনিয়র ল্যাব সহকারী রোগীর পেটের আল্ট্রাসনোগ্রাম রিপোর্ট দেখে ঘাবড়ে গিয়ে রোগীর মুখের ওপর চেঁচিয়ে বলল, *"ওরে সর্বনাশ! আপনার লিভারের টিস্যুতে মিউটেশন কোড 0x7FFF! ব্লাড সেল ডেসিমাল ফেইল করেছে! ল্যাবের অ্যালগরিদম ক্র্যাশ করেছে!"*
- রোগী ভয়ে হার্ট অ্যাটাক করার উপক্রম! রোগীর পরিবারের কাছে এমন সব জটিল ইন্টারনাল ল্যাব ডেটা প্রকাশ পেয়ে গেল যা দেখে তারা আতঙ্কিত হলো এবং হাসপাতালের গোপন ল্যাব কোড সবাই জেনে গেল।

#### সেন্ট্রাল এরর হ্যান্ডলার পদ্ধতি (Standardized Error Envelope):
- হাসপাতালের একজন দক্ষ **ট্রায়াজ ডাক্তার (Global Exception Handler)** আছেন। ল্যাবে কোনো মেশিন খারাপ হলে বা জটিল রিপোর্ট এলে ডাক্তার সাধারণ রোগীকে আতঙ্কের টেকনিক্যাল স্ট্যাকট্রেস দেখতে দেন না।
- তিনি রোগীকে একটি সুন্দর, সুশৃঙ্খল টিকিট (`ErrorResponse Envelope`) ধরিয়ে দিয়ে বলেন:
  - **সমস্যার ধরন (`code`)**: `ORGAN_EXAMINATION_REQUIRED`
  - **সহজ বার্তা (`message`)**: *"আপনার রিপোর্টটি আমাদের বিশেষজ্ঞ ডক্টর রিভিউ করছেন।"*
  - **ট্র্যাকিং নম্বর (`trace_id`)**: `TRC-9843-XY`
- আর ল্যাবের সেই আসল জটিল রিপোর্ট, ভেতরের কোড ও সমস্যার মূল উৎস ডাক্তার হাসপাতালের অভ্যন্তরীণ সুরক্ষিত ভল্টে (`Internal Server Logs`) সংরক্ষিত রাখলেন এই `trace_id`-এর অধীনে। রোগী কেবল তার ট্র্যাকিং আইডি নিয়ে হেল্পডেস্কে গেলেই সাথে সাথে সমাধান পেয়ে যাবে।

আমাদের ব্যাকএন্ডে **ডোমেইন এক্সেপশন** হলো সেই অভ্যন্তরীণ ল্যাব রিপোর্ট, আর **সেন্ট্রাল এক্সেপশন হ্যান্ডলার** হলো সেই বিচক্ষণ ট্রায়াজ ডাক্তার।

---

## ৩. এটা না বানালে কী মহাবিপদ হতো? (The Production Disaster Without It)

### বিপর্যয় ১: তথ্য ফাঁস ও ওডব্লিউএএসপি (OWASP Top 10) সিকিউরিটি ব্রিজ
যখন কোনো প্রোডাকশন ব্যাকএন্ডে সেন্ট্রালাইজড ৫০০ হ্যান্ডলার থাকে না, তখন পাইথন তার ডিফল্ট ট্রেসব্যাক রেসপন্সে পাঠিয়ে দেয়:
```text
Traceback (most recent call last):
  File "/var/app/backend/core/database.py", line 42, in connect
    password="super_secret_db_password_123"
psycopg2.OperationalError: FATAL: password authentication failed for user "postgres"
```
একজন হ্যাকার এই একটি রেসপন্স দেখেই জেনে ফেলল:
1. সার্ভারের ওএস ডিরেক্টরি স্ট্রাকচার (`/var/app/backend/...`)
2. ডাটাবেজ ইঞ্জিনের নাম (`PostgreSQL / psycopg2`)
3. ডাটাবেজের ইউজারনেম ও ইন্টারনাল ক্রেডেনশিয়াল ফ্র্যাগমেন্ট!  
একে বলা হয় **Information Exposure Through an Error Message (CWE-209)**, যা একটি এন্টারপ্রাইজ কোম্পানির জন্য লাইসেন্স বাতিলের কারণ হতে পারে।

### বিপর্যয় ২: রাউটার লেয়ারে বয়লারপ্লেট কোডের জঞ্জাল
প্রতিটি রাউটারে যদি ডেভেলপারদের এভাবে কোড লিখতে হয়:
```python
# ক্লান্তিকর ও নোংরা কোড
@router.get("/{user_id}")
async def get_user(user_id: int):
    try:
        return await service.get_by_id(user_id)
    except UserNotFoundException as e:
        raise HTTPException(404, detail=str(e))
    except DatabaseTimeoutException as e:
        raise HTTPException(503, detail="DB slow")
```
৫০টি রাউটে এই কোড ২৫০ বার কপি-পেস্ট হবে। কোনো একদিন একজন ডেভেলপার একটি `try-except` লিখতে ভুলে যাবেন, আর সাথে সাথে পুরো রাউটটি অরক্ষিত হয়ে পড়বে।

---

## ৪. আমাদের প্রজেক্টের আসল কোড ও কোডের সহজ ব্যাখ্যা (Real Code & Detailed Line-by-Line Breakdown in Bengali)

আসুন আমাদের প্রোডাকশন কোডবেস `app/core/exceptions.py` এবং `app/core/exception_handlers.py` থেকে সরাসরি কোড দেখে নেওয়া যাক:

### ক. ডিকুপল্ড ডোমেইন এক্সেপশন শ্রেণি (`app/core/exceptions.py`)

```python
class BaseDomainException(Exception):
    """ডোমেইন লেয়ারের সমস্ত এক্সেপশনের রুট বেস ক্লাস।"""
    def __init__(self, message: str, code: str = "DOMAIN_ERROR") -> None:
        self.message = message
        self.code = code
        super().__init__(self.message)

class EntityNotFoundException(BaseDomainException):
    """রিসোর্স না পাওয়া গেলে উত্থাপিত হয় (ম্যাপ হবে HTTP 404-এ)।"""
    def __init__(self, message: str = "Requested entity was not found.", code: str = "ENTITY_NOT_FOUND") -> None:
        super().__init__(message=message, code=code)

class EntityConflictException(BaseDomainException):
    """ডুপ্লিকেট বা কনফ্লিক্ট ঘটলে উত্থাপিত হয় (ম্যাপ হবে HTTP 409-এ)।"""
    def __init__(self, message: str = "Entity already exists or conflict occurred.", code: str = "ENTITY_CONFLICT") -> None:
        super().__init__(message=message, code=code)
```

### খ. সেন্ট্রাল হ্যান্ডলার ও নিরাপদ ৫০০ মাস্কিং (`app/core/exception_handlers.py`)

```python
async def domain_exception_handler(
    request: Request,
    exc: BaseDomainException,
) -> JSONResponse:
    """ডোমেইন এক্সেপশনকে স্বয়ংক্রিয়ভাবে স্ট্যান্ডার্ড এরর এনভেলাপে রূপান্তর করে।"""
    status_code = _resolve_domain_status_code(exc)
    trace_id = str(uuid.uuid4())

    error_detail = ErrorDetail(
        code=exc.code,
        message=exc.message,
        status_code=status_code,
        timestamp=datetime.now(timezone.utc),
        trace_id=trace_id,
        details=None,
    )
    error_response = ErrorResponse(error=error_detail, detail=exc.message)

    logger.warning("Domain exception [%s] at '%s': %s (trace_id=%s)", exc.code, request.url.path, exc.message, trace_id)
    return JSONResponse(status_code=status_code, content=error_response.model_dump(mode="json"))


async def unhandled_exception_handler(
    request: Request,
    exc: Exception,
) -> JSONResponse:
    """অনাকাঙ্ক্ষিত ৫০০ ক্র্যাশ ধরে ইন্টারনাল স্ট্যাকট্রেস মাস্ক করে দেয়।"""
    trace_id = str(uuid.uuid4())

    # আসল ট্রেসব্যাক ইন্টারনাল সিকিউর লগে সেভ করা হচ্ছে
    logger.error("Unhandled exception at '%s' (trace_id=%s): %s", request.url.path, trace_id, exc, exc_info=True)

    # ক্লায়েন্টকে পাঠানো হচ্ছে শতভাগ নিরাপদ, স্যানিটাইজড বার্তা
    error_detail = ErrorDetail(
        code="INTERNAL_SERVER_ERROR",
        message="An unexpected internal server error occurred. Please contact support quoting the trace_id.",
        status_code=500,
        timestamp=datetime.now(timezone.utc),
        trace_id=trace_id,
        details=None,
    )
    return JSONResponse(status_code=500, content=ErrorResponse(error=error_detail).model_dump(mode="json"))
```

### কোডের লাইন-বাই-লাইন সহজ ব্যাখ্যা:
- **ডোমেইন এক্সেপশনের স্বাধীনতা**: `BaseDomainException` শুধুই পাইথনের সাধারণ `Exception`-কে ইনহেরিট করেছে। এর সাথে FastAPI, Starlette বা HTTP-র কোনো সম্পর্ক নেই। এর ফলে আমাদের ডোমেইন সার্ভিসগুলোকে ভবিষ্যতে কোনো gRPC, CLI বা ব্যাকগ্রাউন্ড স্ক্রিপ্টেও কোনো পরিবর্তন ছাড়া চালানো যাবে।
- **`uuid.uuid4()` ট্রেস আইডি**: প্রতিটি এররে একটি সম্পূর্ণ ইউনিক ট্র্যাকিং আইডি তৈরি হয়।
- **`logger.error(..., exc_info=True)`**: ৫০০ ক্র্যাশের সম্পূর্ণ স্ট্যাকট্রেস ব্যাকএন্ডের সিকিউর লগ ফাইলে লেখা হয়, যা সাধারণ ইউজার বা আক্রমণকারী কখনোই দেখতে পায় না।
- **মাস্কড রেসপন্স**: ক্লায়েন্ট রেসপন্সে পাইথনের অভ্যন্তরীণ কোডের কোনো চিহ্ন থাকে না; থাকে কেবল একটি ভদ্র জেনেরিক বার্তা এবং `trace_id`।

---

## ৫. ডিএসএ (DSA) সহজ ভাষায় বিশ্লেষণ (DSA Complexity Made Simple)

| প্রক্রিয়া | মেকানিজম / স্ট্রাকচার | টাইম কমপ্লেক্সিটি | স্পেস কমপ্লেক্সিটি | টেকনিক্যাল কারণ |
| :--- | :--- | :--- | :--- | :--- |
| **স্ট্যাটাস কোড রেজোলিউশন** | Class Hierarchy Mapping | $\mathcal{O}(1)$ | $\mathcal{O}(1)$ | `isinstance` চেক এবং ডিকশনারি লুকআপ $\mathcal{O}(1)$ কনস্ট্যান্ট টাইমে হয়। |
| **ভ্যালিডেশন এরর প্রসেসিং** | Pydantic Error Iteration | $\mathcal{O}(K)$ | $\mathcal{O}(K)$ | যেখানে $K$ হলো ইউজারের ভুল ফিল্ডের সংখ্যা ($K \le 10$ সাধারণ রিকোয়েস্টে)। |
| **রেসপন্স সিরিয়ালাইজেশন** | Pydantic v2 Core Export | $\mathcal{O}(1)$ | $\mathcal{O}(1)$ বাউন্ডেড | নির্ধারিত ফিল্ডের ছোট ফিক্সড সাইজ ডিকশনারি তৈরি। |

---

## ৬. ফাস্টএপিআই (FastAPI) ইঞ্জিন কীভাবে কাজ করছে? (FastAPI Internals Simplified)

### এক্সেপশন বাবলিং ও হ্যান্ডলার রেজোলিউশন পাইপলাইন

```
[রাউটার / সার্ভিস লেয়ারে কোনো এরর ঘটল]
                   │
                   ▼
[এক্সেপশন উপরের দিকে উঠতে থাকে (Exception Bubbling)]
                   │
                   ▼
[FastAPI Middleware / ExceptionRouter ইন্টারসেপ্ট করে]
                   │
                   ▼
       [রেজিস্ট্রি ম্যাপে হ্যান্ডলার খোঁজা]
       ┌───────────────────────────┬───────────────────────────┐
       ▼                           ▼                           ▼
isinstance(exc, Domain)?    isinstance(exc, 422)?       Uncaught Exception?
       │                           │                           │
       ▼                           ▼                           ▼
[domain_exception_handler]  [validation_handler]        [unhandled_exception]
       │                           │                           │
       └───────────────────────────┴───────────────────────────┘
                                   ▼
          [JSONResponse with Unified Error Envelope Delivered]
```

FastAPI-তে যখন `app.add_exception_handler(ExceptionType, handler_func)` রেজিস্টার করা হয়, তখন ফ্রেমওয়ার্ক ইন্টারনালি একটি মেথড রেজোলিউশন অর্ডার (MRO) ডিকশনারি মেইনটেইন করে। যখনই কোনো এক্সেপশন রেইজ হয়, সে ইনহেরিট্যান্স চেইনের সবচেয়ে কাছের রেজিস্টার্ড হ্যান্ডলারটিকে খুঁজে বের করে মুহূর্তের মধ্যে কন্ট্রোল তার কাছে সমর্পণ করে।

---

## ৭. এই দিনের RCA ও বাস্তব ভুল থেকে শিক্ষা (RCA Incident, Root Cause & Prevention)

### ইনসিডেন্ট: সিকিউরিটি হেডারের অন্তর্ধান (`WWW-Authenticate` Header Drop)

#### ফেইল করা হ্যান্ডলার কোড:
```python
# ত্রুটিপূর্ণ হ্যান্ডলার
async def bad_http_handler(request: Request, exc: StarletteHTTPException):
    # শুধুই বডি তৈরি করে ফেরত দেওয়া হয়েছে, হেডার বাদ দেওয়া হয়েছে!
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
```

#### রুট কজ (Root Cause):
HTTP 401 Unauthorized রেসপন্সে আরএফসি মানদণ্ড (RFC 7235) অনুসারে সার্ভারকে অবশ্যই রেসপন্স হেডারে `WWW-Authenticate` পাঠাতে হয় যাতে ক্লায়েন্ট বুঝতে পারে কোন স্কিমে অথেন্টিকেশন করতে হবে (যেমন `WWW-Authenticate: ApiKey` বা `Bearer`).  
যদি কাস্টম এক্সেপশন হ্যান্ডলার তৈরির সময় `getattr(exc, "headers", None)` চেক করে সেই হেডারগুলো পাস না করা হয়, তবে সিকিউরিটি হেডার মুছে যায় এবং ব্রাউজার বা এপিআই গেটওয়ে ক্লায়েন্ট বিভ্রান্ত হয়ে পড়ে।

#### সমাধান:
```python
headers = getattr(exc, "headers", None)
return JSONResponse(status_code=status_code, content=content, headers=headers)
```

---

## ৮. ইন্টারভিউ ও ভাইভা প্রশ্ন (Interview Questions & Model Answers)

### প্রশ্ন ১: সার্ভিস লেয়ারে সরাসরি FastAPI-র `HTTPException` রেইজ করা কেন ক্লিন আর্কিটেকচারের চরম লঙ্ঘন?
**উত্তর**:  
ক্লিন আর্কিটেকচারের মূল ভিত্তি হলো **ডিপেনডেন্সি রুল (Dependency Rule)**—ভেতরের ডোমেইন লেয়ার কখনোই বাইরের ট্রান্সপোর্ট বা ডেলিভারি লেয়ার সম্পর্কে জানবে না।  
`HTTPException` হলো ওয়েব/HTTP ট্রান্সপোর্ট লেয়ারের কনসেপ্ট। সার্ভিস লেয়ারের কাজ হলো বিশুদ্ধ বিজনেস লজিক চালানো। আজ এই সার্ভিসটি HTTP এপিআই দিয়ে ব্যবহার হচ্ছে; কাল যদি এটিকে মেসেজ কিউ কনজিউমার (RabbitMQ), সিএলআই টুল, বা gRPC সার্ভিসে ব্যবহার করতে হয়, তখন সার্ভিস লেয়ারের ভেতর `HTTPException(404)` থাকা চরম হাস্যকর ও অসামঞ্জস্যপূর্ণ হবে। সার্ভিস লেয়ারে শুধু ডোমেইন এক্সেপশন (`UserNotFoundException`) ছুড়তে হবে, আর ওয়েব লেয়ারের সেন্ট্রাল হ্যান্ডলার সেটিকে HTTP স্ট্যাটাস কোডে রূপান্তর করবে।

### প্রশ্ন ২: প্রোডাকশনে ৫০০ ইন্টারনাল সার্ভার এররে কেন স্ট্যাকট্রেস ক্লায়েন্টকে না দেখিয়ে ইউনিক `trace_id` ফেরত পাঠানো উচিত?
**উত্তর**:  
দুটি প্রধান কারণে:
1. **নিরাপত্তা (Security)**: স্ট্যাকট্রেস ফাঁস হলে আক্রমণকারী ডাটাবেজের স্কিমা, গোপন কোড পাথ এবং সিস্টেমের অভ্যন্তরীণ দুর্বলতা জেনে ফেলে (OWASP Information Leakage)।
2. **অবজারভেবিলিটি ও সাপোর্ট (Observability & Support)**: কোনো গ্রাহক যখন সাপোর্ট টিমে অভিযোগ জানায়, সে যদি বলে *"সার্ভারে ক্র্যাশ করেছে"*, ডেভেলপারদের পক্ষে লাখ লাখ লগ ফাইলের ভেতর সেই নির্দিষ্ট সমস্যাটি খুঁজে বের করা অসম্ভব। কিন্তু রেসপন্সে একটি ইউনিক `trace_id` (UUIDv4) থাকলে ডেভেলপার সরাসরি সেন্ট্রি (Sentry) বা ইএলকে (ELK) লগে ওই আইডি সার্চ করে এক সেকেন্ডের মধ্যে আসল রুট কজ শনাক্ত করতে পারেন।

---

## ৯. এক নজরে আসল মূল লজিক (The Core Bottom-Line Logic)

> **গোল্ডেন রুলস**:
> 1. ডোমেইন সার্ভিস ও রিপোজিটরিতে কখনোই `HTTPException` আমদানি বা রেইজ করবেন না; সর্বদা ডোমেইন এক্সেপশন ছুড়ুন।
> 2. রাউটারগুলোতে বারবার `try...except` ব্লক লিখবেন না; সেন্ট্রালাইজড এক্সেপশন হ্যান্ডলারের কাছে এরর ম্যাপিং ছেড়ে দিন।
> 3. ৫০০ ইন্টারনাল সার্ভার এররে ক্লায়েন্টের কাছে স্ট্যাকট্রেস সম্পূর্ণ মাস্ক করে দিন এবং সর্বদা একটি ইউনিক `trace_id` সংযুক্ত করুন।
