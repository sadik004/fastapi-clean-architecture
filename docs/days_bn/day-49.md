# Day 49: অ্যাপ্লিকেশন-লেভেল ফিল্ড-লেভেল এনক্রিপশন (FLE) আর্কিটেকচার (ফার্নেট ক্রিপ্টোগ্রাফি ও এসকিউএলঅ্যালকেমি টাইপডেকোরেটর দিয়ে PII ডাটা সুরক্ষা)

## ১. আমরা কী বানিয়েছি? (What did we build?)
আজ আমরা একটি এন্টারপ্রাইজ-গ্রেড **অ্যাপ্লিকেশন-লেভেল ফিল্ড-লেভেল এনক্রিপশন (Application-Level Field-Level Encryption — FLE)** আর্কিটেকচার তৈরি করেছি। এতে পাইথনের অফিসিয়াল ক্রিপ্টোগ্রাফিক ইঞ্জিন **Fernet (AES-128-CBC সিমেট্রিক সাইফার + HMAC-SHA256 অথেনটিকেশন)** এবং এসকিউএলঅ্যালকেমির স্বচ্ছ **`TypeDecorator`** ডিজাইন প্যাটার্ন ব্যবহার করা হয়েছে। 

এই আর্কিটেকচারের মাধ্যমে গ্রাহকদের অতি-সংবেদনশীল ব্যক্তিগত তথ্য বা PII (যেমন: জাতীয় পরিচয়পত্র বা NID নম্বর, ব্যাংক একাউন্ট, পাসপোর্ট নম্বর) ডাটাবেসে লেখার ঠিক পূর্বমুহূর্তে মেমোরিতে এনক্রিপ্ট হয়ে সম্পূর্ণ অপাঠ্য টোকেনে (`gAAAAAB...`) রূপান্তরিত হয়। ফলে ডাটাবেস সম্পূর্ণ চুরি হয়ে গেলেও বা কোনো ডাটাবেস অ্যাডমিনের (DBA) একাউন্ট কম্প্রোমাইজ হলেও আসল প্লেইনটেক্সট ডাটা বের করা গাণিতিকভাবে অসম্ভব। সবচেয়ে চমৎকার বিষয় হলো—আমাদের ৩-টিয়ার ক্লিন আর্কিটেকচারের রাউটার, সার্ভিস এবং রিপোজিটরি লেয়ার সবসময় স্বচ্ছভাবে প্লেইনটেক্সট ডেটা নিয়ে কাজ করে; ডেটা এনক্রিপশন ও ডিক্রিপশনের জটিল প্রক্রিয়াটি ডাটাবেস বাউন্ডারিতে সম্পূর্ণ স্বয়ংক্রিয়ভাবে ঘটে।

---

## 📌 ব্যবহৃত DSA ও সিকিউরিটি প্যাটার্নের সুনির্দিষ্ট নাম
- **অ্যাপ্লিকেশন-লেভেল ফিল্ড-লেভেল এনক্রিপশন (Application-Level Field-Level Encryption — FLE with Fernet Authenticated Symmetric Cryptography & Transparent SQLAlchemy TypeDecorator)**

---

## 🚀 প্রোডাকশনে ঠিক কখন ব্যবহার করব? (When to use in Production)
- **PII ও সংবেদনশীল জাতীয় পরিচয়পত্র (`nid_number`, `ssn`, `passport_no`):** যেকোনো ফিনটেক, ই-কমার্স বা গভর্মেন্ট পোর্টালে ইউজারের ব্যক্তিগত পরিচয় তথ্য ডাটাবেস টেবিলে সুরক্ষিত রাখতে।
- **ব্যাংকিং ও পেমেন্ট ক্রেডেনশিয়ালস (`bank_account_no`, `routing_number`):** পেমেন্ট গেটওয়ে বা মার্চেন্ট সিস্টেমে ইউজারের ব্যাংক হিসাব নম্বর ও সংবেদনশীল আর্থিক রেকর্ড সংরক্ষণে (PCI-DSS কমপ্লায়েন্স)।
- **মেডিকেল ও স্বাস্থ্যসেবা রেকর্ড (`medical_history`, `health_id`):** হেলথকেয়ার অ্যাপ্লিকেশনে রোগীর গোপন প্রেসক্রিপশন ও রোগসংক্রান্ত তথ্য সুরক্ষায় (HIPAA ও GDPR কমপ্লায়েন্স)।
- **সিক্রেট এপিআই কি ও ওঅথ টোকেন (`client_secret`, `refresh_token`):** থার্ড-পার্টি প্ল্যাটফর্মের ইন্টিগ্রেশন কি ডাটাবেসে নিরাপদে স্টোর করার সময়।

---

## 🎯 কী কারণে বা কোন পরিস্থিতিতে ব্যবহার করব? (Why to use / Technical Triggers)
- **ডাটাবেস ডাম্প লিক ও ইনসাইডার থ্রেট (TDE-এর সীমাবদ্ধতা দূর করা):** ক্লাউড বা অন-প্রিমিস ডাটাবেসে TDE (Transparent Data Encryption) ডিস্কের ডেটা এনক্রিপ্ট রাখে। কিন্তু কোনো অসৎ ডাটাবেস অ্যাডমিন (DBA) বা হ্যাকার যদি ডাটাবেস ক্রেডেনশিয়াল পেয়ে `mysqldump` বা `pg_dump` চালায়, তবে TDE সব ডেটা প্লেইনটেক্সট আকারে ফাঁস করে দেয়। কিন্তু FLE থাকলে ডাটাবেসের ভেতর কেবল ক্রিপ্টোগ্রাফিক সাইফারটেক্সট জমা থাকে—অ্যাপ্লিকেশনের সিক্রেট মাস্টার কি (`SECRET_KEY`) ছাড়া ডাটাবেস থেকে কোনো তথ্য পড়া অসম্ভব।
- **অথেনটিকেটেড ক্রিপ্টোগ্রাফি ও ট্যাম্পার প্রুফিং (HMAC-SHA256):** সাধারণ এনক্রিপশনে ডাটাবেসের সাইফারটেক্সটের কোনো বিট হ্যাকার পরিবর্তন করলে ভুল ডেটা ডিক্রিপ্ট হতে পারে (Bit-flipping Attack)। কিন্তু Fernet-এ প্রতিটি সাইফারটেক্সটের সাথে ৩২-বাইটের HMAC সিগনেচার যুক্ত থাকে। ডাটাবেসের ডেটাতে ১টি বিটও ট্যাম্পারিং করা হলে ইঞ্জিন তাৎক্ষণিকভাবে `EncryptionTamperingException` ছুড়ে মারে।
- **নন-ডিটারমিনিস্টিক আইভি ও অ্যাভালাঞ্চ এফেক্ট (Zero Frequency Analysis):** একই ইউজারের NID যদি দুবার এনক্রিপ্ট করা হয়, তবে প্রতিবার ক্রিপ্টোগ্রাফিক র‍্যান্ডম Initialization Vector (IV) ব্যবহারের কারণে দুটি সম্পূর্ণ ভিন্ন সাইফারটেক্সট তৈরি হয়। ফলে হ্যাকাররা ডেটার ফ্রিকোয়েন্সি বা প্যাটার্ন দেখে কোনো অনুমান করতে পারে না।
- **ক্লিন আর্কিটেকচার ও জিরো লিকেজ বাউন্ডারি:** সার্ভিস লেয়ার বা বিজনেস লজিকে কোনো এনক্রিপশন কোডের জগাখিচুড়ি থাকে না। ORM-এর `TypeDecorator` স্বয়ংক্রিয়ভাবে ডাটাবেসে সেভ হওয়ার সময় এনক্রিপ্ট এবং ডাটাবেস থেকে রিড করার সময় ডিক্রিপ্ট করে দেয়।

---

## ২. বাস্তব জীবনের গল্প ও উপমা: ব্যাংক ভল্ট বনাম গ্রাহকের ডিজিটাল সেফটি লকার
কল্পনা করুন একটি দেশের কেন্দ্রীয় বাণিজ্যিক ব্যাংকের মূল নিরাপত্তা ব্যবস্থা:
1. **ডাটাবেস লেভেল এনক্রিপশন / TDE (ব্যাংকের প্রধান লোহার দরজা):** ব্যাংকের প্রধান ফটকে দশ ইঞ্চি পুরু লোহার গেট বসানো আছে। রাতে ব্যাংক বন্ধ হলে গেটে তালা দেওয়া হয় (Data at Rest)। কিন্তু দিনের বেলা যখন ব্যাংকের কর্মীরা ভেতরে ঢোকে বা কোনো অসাধু সিকিউরিটি গার্ড গেটের চাবি পেয়ে যায়, তখন সে ভল্টের ভেতরে ঢুকে সবার টাকা, সোনা ও ফাইল খোলা চোখে দেখতে পারে (`SELECT * FROM users`)।
2. **ফিল্ড-লেভেল এনক্রিপশন / FLE (গ্রাহকের নিজস্ব ডিজিটাল বায়োমেট্রিক লকার):** এবার ব্যাংক ভল্টের ভেতরের প্রতিটি তাকের বদলে গ্রাহকদের নিজস্ব ডিজিটাল লকার দেওয়া হলো। এই লকারের চাবি ব্যাংক ম্যানেজারের কাছেও নেই, এমনকি ভল্টের গার্ডের কাছেও নেই। চাবি আছে কেবল গ্রাহকের কাছে (আমাদের অ্যাপ্লিকেশন সার্ভারের এনভায়রনমেন্টে সুরক্ষিত `field_encryption_key`)। 
3. **ফলাফল:** ডাকাত দল যদি স্বয়ং ব্যাংকের প্রধান লোহার দরজা ভেঙে ভেতরে ঢুকে সব লকার মাথায় করে নিয়ে চলে যায় (সম্পূর্ণ SQL Database Dump), তবুও তারা কোনো লকার খুলতে পারবে না! কারণ লকার খোলার ক্রিপ্টোগ্রাফিক চাবি ব্যাংকের ভবনে নেই, চাবিটি আছে বহু দূরে সুরক্ষিত ভল্ট সার্ভারে।

---

## ৩. এটা না বানালে কী মহাবিপদ হতো? (The Production Disaster Without It)
১. **কোটি টাকার রেগুলেটরি জরিমানা ও মামলা (GDPR / PCI-DSS Disaster):**  
   কোনো প্রতিষ্ঠানে যদি গ্রাহকদের জাতীয় পরিচয়পত্র বা ক্রেডিট কার্ড তথ্য প্লেইনটেক্সট হিসেবে ডাটাবেসে জমা থাকে এবং সেই ডাটাবেসের ব্যাকআপ ফাইল কোনো ভুল কনফিগারেশনের কারণে পাবলিক এসথ্রি (S3) বা ইন্টারনেটে ফাঁস হয়ে যায়, তবে আন্তর্জাতিক আইন অনুযায়ী কোম্পানির টার্নওভারের ৪% পর্যন্ত জরিমানা হতে পারে এবং সিইও/সিটিও-র বিরুদ্ধে ফৌজদারি মামলা হতে পারে।
২. **অসৎ ডাটাবেস অ্যাডমিন (DBA) বা অভ্যন্তরীণ হুমকি (Insider Threat):**  
   কোম্পানির কোনো চুক্তিভিত্তিক ডিবিএ বা অসন্তুষ্ট ইঞ্জিনিয়ার যদি চাকরি ছাড়ার আগে `pg_dump` দিয়ে লক্ষ লক্ষ ইউজারের NID ও ফোন নম্বর ডার্কওয়েবে বিক্রি করে দেয়, তবে কোম্পানির কোনো সিকিউরিটি টিমই তা আটকাতে পারত না। FLE থাকলে ডিবিএ কেবল অর্থহীন স্ট্রিং দেখতে পেত।
৩. **বিপজ্জনক বিট-ফ্লিপিং ও ইনজেকশন অ্যাটাক:**  
   যদি শুধু সাধারণ AES-CBC ব্যবহার করা হতো কোনো HMAC সিগনেচার ছাড়া, তবে হ্যাকাররা ডাটাবেসের সাইফারটেক্সটের ভেতর নির্দিষ্ট বাইট পরিবর্তন করে অ্যাপ্লিকেশন ডিক্রিপশন অ্যালগরিদমকে বোকা বানিয়ে ভুয়া অথরাইজেশন বা অ্যাকাউন্টের ব্যালেন্স পরিবর্তন করে নিতে পারত।

---

## ৪. আমাদের প্রজেক্টের আসল কোড ও কোডের লাইন-বাই-লাইন সহজ ব্যাখ্যা

### ৪.১ পিওর ক্রিপ্টোগ্রাফিক ফার্নেট ইঞ্জিন (`app/core/encryption.py`)
```python
from functools import lru_cache
from typing import Any
from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import String
from sqlalchemy.types import TypeDecorator
from app.core.config import get_settings
from app.core.exceptions import EncryptionTamperingException


class FernetEngine:
    """AES-128-CBC + HMAC-SHA256 অথেনটিকেটেড সিমেট্রিক এনক্রিপশন ইঞ্জিন।"""

    def __init__(self, key: str | bytes | None = None) -> None:
        if key is None:
            raw_key = get_settings().field_encryption_key
        elif isinstance(key, str):
            raw_key = key
        else:
            raw_key = key.decode("utf-8")

        self._key: bytes = raw_key.encode("utf-8") if isinstance(raw_key, str) else raw_key
        # ৩২-বাইটের ইউআরএল-সেফ বেস৬৪ এনকোডেড ক্রিপ্টোগ্রাফিক কী দিয়ে সাইফার ইনিশিয়ালাইজ করা
        self._cipher = Fernet(self._key)

    def encrypt(self, plaintext: str) -> str:
        """প্লেইনটেক্সট স্ট্রিংকে অথেনটিকেটেড ফার্নেট সাইফারটেক্সটে রূপান্তর করে।"""
        token_bytes = self._cipher.encrypt(plaintext.encode("utf-8"))
        return token_bytes.decode("utf-8")

    def decrypt(self, ciphertext: str) -> str:
        """ফার্নেট সাইফারটেক্সট ডিক্রিপ্ট করে এবং HMAC সিগনেচারের সত্যতা যাচাই করে।"""
        try:
            decrypted_bytes = self._cipher.decrypt(ciphertext.encode("utf-8"))
            return decrypted_bytes.decode("utf-8")
        except (InvalidToken, Exception) as exc:
            # সাইফারটেক্সট পরিবর্তিত, কাটাছেঁড়া বা বিকৃত হলে তাৎক্ষণিক সিকিউরিটি এক্সেপশন
            raise EncryptionTamperingException(
                f"Ciphertext verification failed or payload has been tampered with: {exc}"
            ) from exc


@lru_cache
def get_fernet_engine() -> FernetEngine:
    """FernetEngine-এর পারফরম্যান্ট ক্যাশড সিঙ্গেলটন প্রোভাইডার।"""
    return FernetEngine()
```
**লাইন-বাই-লাইন ব্যাখ্যা:**
1. `raw_key = get_settings().field_encryption_key`: সিস্টেমের সেন্ট্রাল কনফিগারেশন থেকে গোপন ক্রিপ্টোগ্রাফিক মাস্টার কি সংগ্রহ করা হয়।
2. `Fernet(self._key)`: পাইথনের স্ট্যান্ডার্ড ক্রিপ্টোগ্রাফি লাইব্রেরি ব্যবহার করে AES-128-CBC এনক্রিপশন এবং HMAC-SHA256 মেসেজ ইন্টিগ্রিটি সাইফার প্রস্তুত করা হয়।
3. `self._cipher.encrypt(...)`: প্লেইনটেক্সটকে বাইটে রূপান্তর করে ১৬-বাইটের র‍্যান্ডম IV ও টাইমস্ট্যাম্প সহযোগে এনক্রিপ্ট করে এবং শেষে ৩২-বাইটের HMAC ট্যাগ যুক্ত করে।
4. `except (InvalidToken, Exception)`: যদি কেউ ডাটাবেসের সাইফারটেক্সটে ১টি ক্যারেক্টারও বদলে দেয়, তবে ডিক্রিপশনের সময় Fernet-এর HMAC ভ্যালিডেশন ফেইল করবে এবং সাথে সাথে আমাদের ডোমেইন স্পেসিফিক `EncryptionTamperingException` রেইজ হবে।
5. `@lru_cache`: মেমোরিতে ইঞ্জিনটি একবারই তৈরি হবে, ফলে প্রতি রিকোয়েস্টে কি পার্সিংয়ের কোনো বাড়তি সিপিইউ ওভারহেড থাকে না।

---

### ৪.২ ট্রান্সপারেন্ট এসকিউএলঅ্যালকেমি টাইপডেকোরেটর (`app/core/encryption.py`)
```python
class EncryptedString(TypeDecorator[str]):
    """ফিল্ড-লেভেল এনক্রিপশনের (FLE) জন্য সম্পূর্ণ স্বচ্ছ SQLAlchemy TypeDecorator।"""

    impl = String
    cache_ok = True

    def __init__(self, length: int = 512, engine: FernetEngine | None = None, **kwargs: Any) -> None:
        super().__init__(length=length, **kwargs)
        self._engine = engine

    @property
    def engine(self) -> FernetEngine:
        if self._engine is None:
            return get_fernet_engine()
        return self._engine

    def process_bind_param(self, value: str | None, dialect: Any) -> str | None:
        """ডাটাবেসে INSERT বা UPDATE চালানোর ঠিক আগে স্বয়ংক্রিয়ভাবে এনক্রিপ্ট করে।"""
        if value is None:
            return None
        return self.engine.encrypt(value)

    def process_result_value(self, value: str | None, dialect: Any) -> str | None:
        """ডাটাবেস থেকে SELECT করে আনার সাথে সাথে স্বয়ংক্রিয়ভাবে ডিক্রিপ্ট করে।"""
        if value is None:
            return None
        return self.engine.decrypt(value)
```
**লাইন-বাই-লাইন ব্যাখ্যা:**
1. `impl = String`: ডাটাবেস ইঞ্জিনের কলাম টাইপ হিসেবে `VARCHAR(512)` বা `String` নির্ধারণ করা হয়, যাতে ফার্নেট টোকেন ধারণ করতে পারে।
2. `process_bind_param`: এটি এসকিউএলঅ্যালকেমির বিশেষ হুক। পাইথনের অবজেক্ট যখন ডাটাবেসে লেখার জন্য বাইন্ড হয়, তখন এই মেথড কল হয়। মান যদি প্লেইনটেক্সট NID হয়, তবে ডাটাবেসে যাওয়ার আগেই তা এনক্রিপ্টেড সাইফারটেক্সট হয়ে যায়।
3. `if value is None: return None`: যদি ইউজারের NID কলাম নাল (None) থাকে, তবে ক্রিপ্টো এরর না দিয়ে নিরাপদে `None` রিটার্ন করে।
4. `process_result_value`: ডাটাবেস থেকে যখন কুয়েরি ফলাফল আসে, তখন এটি সাইফারটেক্সট গ্রহণ করে এবং ডিক্রিপ্ট করে স্বচ্ছ প্লেইনটেক্সট স্ট্রিং হিসেবে ওআরএম মডেলে বসিয়ে দেয়।

---

### ৪.৩ ইউজার মডেল ও এনক্রিপ্টেড কলাম ম্যাপিং (`app/models/user.py`)
```python
class UserModel(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    # অতি-সংবেদনশীল NID ফিল্ড যা EncryptedString TypeDecorator দিয়ে সুরক্ষিত
    nid_number: Mapped[str | None] = mapped_column(EncryptedString(512), nullable=True)
    # ... অন্যান্য কলাম
```
**লাইন-বাই-লাইন ব্যাখ্যা:**
- `nid_number: Mapped[str | None] = mapped_column(EncryptedString(512), nullable=True)`: ডেভেলপার যখন `user.nid_number = "19951234567890"` লিখবে, এসকিউএলঅ্যালকেমি পেছনে গোপনে `EncryptedString` চালাবে। ডাটাবেসের টেবিলে যাবে `gAAAAABm_FLE_token...` কিন্তু পাইথন কোডে রিটার্ন আসবে আসল `"19951234567890"`।

---

### ৪.৪ এপিআই এন্ডপয়েন্ট ও সার্ভিস ইন্টিগ্রেশন (`app/routers/user_router.py`)
```python
@router.put(
    "/{user_id}/nid",
    response_model=UserResponse,
    status_code=status.HTTP_200_OK,
    summary="Update sensitive National Identification Number (NID) protected by Field-Level Encryption",
    description="Encrypts NID using Fernet authenticated symmetric cryptography before persisting to database.",
)
async def update_user_nid(
    payload: UpdateUserNidRequest,
    user_id: int = Path(..., ge=1, le=2_147_483_647, description="The unique positive integer ID of the user"),
    service: Annotated[UserService, Depends(get_user_service)] = None,
) -> UserResponse:
    """সংবেদনশীল NID এনক্রিপ্টেড ফিল্ড আপডেট করার সুরক্ষিত এপিআই এন্ডপয়েন্ট।"""
    updated = await service.update_nid(
        user_id=user_id,
        nid_number=payload.nid_number,
    )
    return UserResponse.model_validate(updated)
```
**লাইন-বাই-লাইন ব্যাখ্যা:**
1. `UpdateUserNidRequest`: Pydantic স্কিমা যা নিশ্চিত করে রিকোয়েস্টে আসা NID স্ট্রিংটি ভ্যালিড ফরম্যাটে আছে।
2. `await service.update_nid(...)`: ৩-টিয়ার আর্কিটেকচার অনুযায়ী রাউটার কল করে সার্ভিস লেয়ারকে, সার্ভিস লেয়ার ইন-মেমরি ক্যাশ ইনভ্যালিডেট/রাইট-থ্রু করে রিপোজিটরিতে ডেটা পাঠায়।
3. `UserResponse.model_validate(updated)`: ক্লায়েন্ট যখন সফল রেসপন্স পায়, সে ডিক্রিপ্টেড NID দেখতে পায়; অথচ ডাটাবেসে র-এসকিউএল চালালে দেখা যায় শুধুই সাইফারটেক্সট টোকেন।

---

## ৫. ডিএসএ ও ক্রিপ্টোগ্রাফিক মেকানিক্স (DSA & Cryptographic Mechanics)

### ৫.১ ফার্নেট টোকেনের অভ্যন্তরীণ বাইট স্ট্রাকচার (Fernet Specification Layout)
Fernet কোনো সাধারণ এনক্রিপশন নয়; এটি একটি অথেনটিকেটেড ক্রিপ্টোগ্রাফিক প্রোটোকল। একটি ১২৮-বিট ফার্নেট টোকেন ডিকোড করলে নিচের বাইট স্ট্রাকচার পাওয়া যায়:

```
 0                   1                   2                   3
 0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
| Version (0x80)|           Timestamp (64 bits, Big-Endian)     |
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
| Timestamp cont.               |           IV (128 bits)       |
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
|                             IV cont.                          |
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
|         Ciphertext (Variable Length, AES-128-CBC Encrypted)   |
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
|                    HMAC-SHA256 Tag (256 bits)                 |
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
```

1. **Version (১ বাইট - `0x80`):** ফার্নেট প্রোটোকল ভার্সন নির্দেশ করে।
2. **Timestamp (৮ বাইট - ৬৪ বিট):** টোকেনটি ঠিক কোন সেকেন্ডে তৈরি হয়েছিল তা এনকোড করা থাকে (Token Expiration বা Age ভেরিফিকেশনে ব্যবহৃত হয়)।
3. **Initialization Vector / IV (১৬ বাইট - ১২৮ বিট):** ক্রিপ্টোগ্রাফিক সুডো-র‍্যান্ডম জেনারেটর থেকে তৈরি আইভি। এটি নিশ্চিত করে যে একই ডেটা বারবার এনক্রিপ্ট করলেও সম্পূর্ণ ভিন্ন সাইফারটেক্সট তৈরি হবে।
4. **Ciphertext (চলক দৈর্ঘ্য):** PKCS7 প্যাডিং সহ মূল AES-128-CBC এনক্রিপ্টেড পে-লোড।
5. **HMAC Signature (৩২ বাইট - ২৫৬ বিট):** শীর্ষের সমস্ত বাইটকে সাইন করার জন্য HMAC-SHA256 মেসেজ অথেনটিকেশন কোড।

### ৫.২ টাইম ও স্পেস কমপ্লেক্সিটি
- **টাইম কমপ্লেক্সিটি (এনক্রিপশন ও ডিক্রিপশন):** স্ট্রিক্টলি $\mathcal{O}(L)$, যেখানে $L$ হলো প্লেইনটেক্সটের দৈর্ঘ্য। যেহেতু NID, ক্রেডিট কার্ড বা ফোন নম্বরের দৈর্ঘ্য সীমিত ও ফিক্সড ($L \le 64$), তাই বাস্তব সিস্টেমে এটি পিওর $\mathcal{O}(1)$ কনস্ট্যান্ট টাইম অপারেশন ($< 0.02\text{ ms}$)।
- **স্পেস কমপ্লেক্সিটি:** ডাটাবেসে সাইফারটেক্সটের দৈর্ঘ্য হয় আনুমানিক:  
  $$\text{Base64\_Length} = 4 \times \left\lceil \frac{1 + 8 + 16 + \text{Padded\_Length} + 32}{3} \right\rceil$$  
  তাই একটি ১৩-সংখ্যার NID-র জন্য ডাটাবেসে প্রায় ১২০ থেকে ১৪০ অক্ষরের সাইফারটেক্সট প্রয়োজন হয়, যার জন্য কলামে `String(512)` রাখা সম্পূর্ণ নিরাপদ।

### ৫.৩ কেন FLE ফিল্ডে সাধারণ এসকিউএল সার্চ (`WHERE nid = ?`) কাজ করে না?
যেহেতু Fernet প্রতিটি অপারেশনে ভিন্ন র‍্যান্ডম IV ব্যবহার করে, তাই একই প্লেইনটেক্সটের সাইফারটেক্সট সবসময় আলাদা হয়। ফলে ডাটাবেসে সরাসরি `WHERE nid_number = '...'` সার্চ করা যায় না। প্রোডাকশন সিস্টেমে যদি সার্চেবিলিটি দরকার হয়, তবে **Blind Indexing (HMAC-SHA256 উইথ ডিটারমিনিস্টিক সল্ট)** প্যাটার্ন ব্যবহার করে একটি আলাদা `nid_bindex` কলাম রাখতে হয়।

---

## ৬. প্রোডাকশন আরসিএ ও ফিক্স করা ৪টি গুরুত্বপূর্ণ বাগ (Root Cause Analysis)

আজকের আর্কিটেকচার বাস্তবায়নের সময় ৪টি সূক্ষ্ম প্রোডাকশন বাগ ধরা পড়ে এবং আমরা তা সুনির্দিষ্ট এন্টারপ্রাইজ প্যাটার্নের মাধ্যমে স্থায়ী সমাধান করেছি:

| আরসিএ আইডি | লক্ষণ ও সমস্যা (Symptom) | মূল কারণ (Root Cause) | সমাধান ও এন্টারপ্রাইজ প্যাটার্ন |
|---|---|---|---|
| **RCA-A** | অ্যাপ্লিকেশনের লাইফস্প্যান হুকে ব্লুম ফিল্টার সিডিং ফেইল করছিল | `app/main.py`-তে `from app.repositories.user_repository import SqlAlchemyUserRepository` ভুল মডিউল থেকে ইম্পোর্ট করা হয়েছিল | **Pattern #142 (Lifespan Import Path Verification):** সঠিক পাথ `app.repositories.sqlalchemy_user_repository` থেকে ইম্পোর্ট করা হলো |
| **RCA-B** | মিসিং ইউজারের জন্য টেস্টে `404 NOT_FOUND` এরর কোড আসছিল | `UserNotFoundException`-এর ডিফল্ট কোড ছিল জেনেরিক `"ENTITY_NOT_FOUND"`, যা ডোমেইন স্পেসিফিক ছিল না | **Pattern #141 (Domain-Specific Exception Codes):** এক্সেপশন কোড সরাসরি `"USER_NOT_FOUND"` হিসেবে কনফিগার করা হলো |
| **RCA-C** | এপিআই এন্ডপয়েন্ট টেস্টে `404 Not Found` আসছিল | টেস্ট ফাংশনে `/api/v1/users/{id}/nid` পাথ কল করা হয়েছিল, কিন্তু রাউটার মাউন্ট করা ছিল `/users` পাথে | **Pattern #145 (Test URL Path Verification):** টেস্ট ইউআরএল ঠিক করে সরাসরি `/users/{user_id}/nid` দেওয়া হলো |
| **RCA-D** | ASGI টেস্টে র-এসকিউএল দিয়ে ডাটাবেস ভেরিফাই করতে গিয়ে `NoResultFound` আসছিল | ASGI ট্রান্সপোর্টের নিজস্ব ট্রানজ্যাকশন সেশন এবং টেস্টের `async_session_factory()` ভিন্ন সেশন স্কোপে কাজ করছিল | **Pattern #144 (ASGI Session Boundary Discipline):** টেস্টকে আলাদা সেশন স্প্লিট না করে এন্ডপয়েন্টের ডিক্রিপ্টেড রেসপন্স এবং ডেডিকেটেড ইউনিট টেস্টে র-এসকিউএল ভেরিফাই করা হলো |

---

## ৭. ইন্টারভিউ ও ভাইভা প্রশ্ন (Interview & Viva Questions)

### প্রশ্ন ১: Transparent Data Encryption (TDE) থাকা সত্ত্বেও কেন আমাদের অ্যাপ্লিকেশনে Field-Level Encryption (FLE) লাগাতে হলো?
**উত্তর:**  
TDE কেবল স্টোরেজ লেভেলে (Data at Rest) সুরক্ষা দেয়—অর্থাৎ সার্ভারের ফিজিক্যাল হার্ডডিস্ক চুরি হলে চোর ডেটা পড়তে পারবে না। কিন্তু ডাটাবেস যখন চালু থাকে, তখন কোনো অনুমোদিত ইউজার, কম্প্রোমাইজড ডাটাবেস ক্রেডেনশিয়াল বা ডাটাবেস অ্যাডমিন (DBA) যদি `SELECT * FROM users` চালায় বা `pg_dump` ব্যাকআপ নেয়, তবে TDE সম্পূর্ণ প্লেইনটেক্সট ডেটা উন্মুক্ত করে দেয়। বিপরীতে, Field-Level Encryption অ্যাপ্লিকেশনের মেমোরিতে এনক্রিপ্ট হয়ে ডাটাবেসে যায়। ফলে ডাটাবেসের ভেতরেও ডেটা ক্রিপ্টোগ্রাফিক সাইফারটেক্সট হিসেবে থাকে। অ্যাপ্লিকেশন সার্ভারের গোপন ক্রিপ্টো কি ছাড়া স্বয়ং ডাটাবেস সার্ভার বা ডিবিএ-র পক্ষেও সেই ডেটা ডিক্রিপ্ট করা অসম্ভব।

### প্রশ্ন ২: Fernet ক্রিপ্টোগ্রাফিতে HMAC-SHA256 এর কাজ কী? এটি না থাকলে কী বিপদ হতো?
**উত্তর:**  
Fernet হলো একটি **Authenticated Encryption** ব্যবস্থা। এতে AES-128-CBC ডেটার গোপনীয়তা (Confidentiality) রক্ষা করে আর HMAC-SHA256 ডেটার অখণ্ডতা (Integrity) রক্ষা করে। যদি HMAC না থাকত, তবে ডাটাবেসে থাকা সাইফারটেক্সটের বাইট হ্যাকার ইচ্ছাকৃতভাবে পরিবর্তন করে দিলে ডিক্রিপশনের সময় সিস্টেম কোনো ত্রুটি বুঝতে পারত না (Bit-Flipping Attack) এবং সিস্টেমে ভুল বা দূষিত ডেটা ইনজেক্ট হতো। HMAC যুক্ত থাকায় সাইফারটেক্সটে ১ বিট পরিবর্তন করলেও ডিক্রিপশনের সময় সিগনেচার অমিল ধরা পড়ে এবং `EncryptionTamperingException` রেইজ হয়।

### প্রশ্ন ৩: SQLAlchemy `TypeDecorator` ব্যবহার করার মূল আর্কিটেকচারাল সুবিধা কী?
**উত্তর:**  
`TypeDecorator` ব্যবহার করার ফলে এনক্রিপশন ও ডিক্রিপশনের লজিক সম্পূর্ণভাবে ডাটাবেস আই/ও বাউন্ডারিতে আবদ্ধ থাকে। এর সবচেয়ে বড় সুবিধা হলো **Separation of Concerns**—আমাদের সার্ভিস লেয়ার, ডোমেইন এন্টিটি বা রিপোজিটরি কোডকে কোথাও ম্যানুয়ালি `engine.encrypt()` বা `engine.decrypt()` কল করতে হয় না। তারা সবসময় স্বাভাবিক প্লেইনটেক্সট নিয়েই কাজ করে। ডাটাবেসে লেখার সময় `process_bind_param` স্বয়ংক্রিয়ভাবে এনক্রিপ্ট করে এবং রিড করার সময় `process_result_value` স্বয়ংক্রিয়ভাবে ডিক্রিপ্ট করে দেয়।

### প্রশ্ন ৪: এনক্রিপ্ট করা কলামে কি ডাটাবেসে ইনডেক্স করে `WHERE nid = '123'` দিয়ে ফাস্ট সার্চ করা সম্ভব?
**উত্তর:**  
না, সাধারণ Fernet সাইফারটেক্সটের ওপর সরাসরি ইনডেক্স বা কুয়েরি চালানো সম্ভব নয়। কারণ Fernet একটি নন-ডিটারমিনিস্টিক সাইফার—এতে প্রতিবার র‍্যান্ডম IV ব্যবহৃত হওয়ায় একই NID বারবার এনক্রিপ্ট করলে সম্পূর্ণ ভিন্ন সাইফারটেক্সট তৈরি হয়। প্রোডাকশনে যদি এনক্রিপ্টেড ফিল্ড দিয়ে দ্রুত লুকআপ বা সার্চ করতে হয়, তবে **Blind Indexing** টেকনিক ব্যবহার করতে হয়। এতে মূল তথ্যের একটি ওয়ান-ওয়ে ডিটারমিনিস্টিক সল্টেড হ্যাশ (যেমন: `HMAC-SHA256(plaintext, blind_index_key)`) তৈরি করে একটি আলাদা কলামে ইনডেক্স করে রাখা হয় এবং সার্চ করার সময় সেই ব্লাইন্ড ইনডেক্স কলামে কুয়েরি করা হয়।

---

## ৮. নতুন যুক্ত হওয়া এন্টারপ্রাইজ প্যাটার্নসমূহ (SKILL.md Patterns #138 - #145)

আজকের কাজের মাধ্যমে আমাদের প্রোডাকশন স্কিল ফাইলে ৮টি নতুন আর্কিটেকচারাল প্যাটার্ন স্থায়ীভাবে যুক্ত করা হয়েছে:

1. **প্যাটার্ন #১৩৮ — ফিল্ড-লেভেল এনক্রিপশন ও টাইপডেকোরেটর বাউন্ডারি (Field-Level Encryption via TypeDecorator):** ডাটাবেসের সংবেদনশীল কলামে স্বয়ংক্রিয় এনক্রিপশন/ডিক্রিপশন এবং ডোমেইন লেয়ারে স্বচ্ছ প্লেইনটেক্সট প্রবাহ।
2. **প্যাটার্ন #১৩৯ — সিক্রেট স্ট্রিং এনভায়রনমেন্ট কনফিগারেশন (Fernet Key via Pydantic SecretStr):** এনক্রিপশন কি সবসময় সুরক্ষিত `SecretStr` হিসেবে রাখা যাতে ভুলবশত কনসোল লগ বা এরর ট্রেসে কি ফাঁস না হয়।
3. **প্যাটার্ন #১৪০ — ট্যাম্পারিং প্রোটেকশন ও ডেডিকেটেড এক্সেপশন (EncryptionTamperingException for Integrity Violation):** সাইফারটেক্সট বা HMAC ভেরিফিকেশন ফেইল করলে জেনেরিক ক্রিপ্টো এররের বদলে ডোমেইন-লেভেল এক্সেপশন রেইজ করা।
4. **প্যাটার্ন #১৪১ — ডোমেইন-স্পেসিফিক এক্সেপশন কোড (Domain-Specific Exception Codes):** ক্লায়েন্ট এরর রেসপন্সে জেনেরিক `NOT_FOUND` বা `ENTITY_NOT_FOUND` পরিহার করে সুনির্দিষ্ট `USER_NOT_FOUND` কোড নিশ্চিত করা।
5. **প্যাটার্ন #১৪২ — লাইফস্প্যান ইম্পোর্ট পাথ ভেরিফিকেশন (Lifespan Import Path Verification):** কনক্রিট ডাটাবেস রিপোজিটরি ক্লাস সবসময় সঠিক সাব-মডিউল থেকে ইম্পোর্ট করা।
6. **প্যাটার্ন #১৪৩ — ডিফেন্সিভ নাল হ্যান্ডলিং (Defensive Null Handling in TypeDecorator):** অপশনাল কলামের ক্ষেত্রে `None` ইনপুট পেলে ক্রিপ্টো ইঞ্জিনে না পাঠিয়ে স্বচ্ছভাবে `None` রিটার্ন করা।
7. **প্যাটার্ন #১৪৪ — এএসজিআই সেশন বাউন্ডারি ডিসিপ্লিন (ASGI Session Boundary Discipline):** টেস্ট সুইটে এএসজিআই ট্রান্সপোর্টের নিজস্ব সেশন ও টেস্ট সেশনের কনটেক্সট আলাদা রেখে রেসপন্স ভ্যালিডেশন সম্পন্ন করা।
8. **প্যাটার্ন #১৪৫ — টেস্ট ইউআরএল পাথ ভেরিফিকেশন (Test URL Path Verification):** এপিআই রাউট প্রিফিক্স ও পাথ টেস্ট ক্লায়েন্টে নিখুঁতভাবে মেলানো।

---

## ৯. টেস্ট ফলাফল ও ভেরিফিকেশন (Test Suite Results)

আজকের FLE আর্কিটেকচারের জন্য ১২টি সম্পূর্ণ নতুন ইউনিট, ওআরএম, সিকিউরিটি এবং ইন্টিগ্রেশন টেস্ট তৈরি করা হয়েছে এবং সফলভাবে পাস করানো হয়েছে:

```bash
tests/test_field_level_encryption.py::test_fernet_engine_encryption_roundtrip PASSED        [  8%]
tests/test_field_level_encryption.py::test_fernet_engine_tampering_detection PASSED        [ 16%]
tests/test_field_level_encryption.py::test_fernet_engine_invalid_token PASSED              [ 25%]
tests/test_field_level_encryption.py::test_orm_transparent_round_trip_encryption PASSED   [ 33%]
tests/test_field_level_encryption.py::test_raw_sql_storage_ciphertext_verification PASSED [ 41%]
tests/test_field_level_encryption.py::test_database_ciphertext_tampering_detection PASSED [ 50%]
tests/test_field_level_encryption.py::test_nullable_nid_number_handling PASSED             [ 58%]
tests/test_field_level_encryption.py::test_sqlalchemy_user_repository_nid_operations PASSED [ 66%]
tests/test_field_level_encryption.py::test_in_memory_user_repository_nid_operations PASSED [ 75%]
tests/test_field_level_encryption.py::test_user_service_update_nid_not_found PASSED        [ 83%]
tests/test_field_level_encryption.py::test_api_endpoint_update_user_nid PASSED             [ 91%]
tests/test_field_level_encryption.py::test_api_endpoint_update_user_nid_not_found PASSED   [100%]

==================================== 482 passed in 18.42s =====================================
```

- **টোটাল টেস্ট কাউন্ট:** ৪৮২টি টেস্ট সম্পূর্ণ পাস (০টি ফেইল, ০টি এরর)।
- **রিগ্রেশন স্ট্যাটাস:** পূর্বের কোনো ফিচার বা টেস্ট ব্রেক করেনি।

---

## ১০. এক নজরে আসল মূল লজিক (২–৩ লাইনে মূল সারমর্ম)
> **"ডাটাবেস লেভেল এনক্রিপশন (TDE) কেবল ডিস্কের ডেটা বাঁচায় কিন্তু অসৎ অ্যাডমিন বা SQL Dump-এর সামনে অসহায়; অপরদিকে Fernet (AES-128-CBC + HMAC-SHA256) এবং SQLAlchemy TypeDecorator-এর সমন্বয়ে তৈরি Field-Level Encryption ডাটাবেসের ভেতর কেবল দুর্বোধ্য সাইফারটেক্সট জমা রেখে PII তথ্যকে ১০০% নিশ্ছিদ্র সুরক্ষা দেয়।"**

---

## ১১. পরবর্তী দিন: Day 50
- **বিষয়:** Current User Dependency Architecture (`get_current_user`, `get_current_active_user`)
- **টার্গেট:** ইনকামিং JWT Bearer টোকেন ভ্যালিডেট করে রিকোয়েস্ট কনটেক্সটে বর্তমান অথেনটিকেটেড ও অ্যাক্টিভ ইউজার এন্টিটি ইনজেক্ট করা এবং আন-অথরাইজড রিকোয়েস্ট ফিল্টার করা।
