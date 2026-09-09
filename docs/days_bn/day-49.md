# দিন ৪৯ — Application-Level Field-Level Encryption (FLE)
### Fernet Cryptography ও SQLAlchemy TypeDecorator আর্কিটেকচার

**তারিখ**: ২০২৬-০৯-০৯  
**স্ট্যাটাস**: ✅ সম্পূর্ণ | **টেস্ট**: ৪৮২ পাশ, ০ ফেল

---

## ১. কেন FLE দরকার? — "Database Breach" সমস্যাটা কী?

Database-level encryption (TDE) শুধু disk-এ থাকা data রক্ষা করে — at rest।
কিন্তু database credentials পেলে বা SQL dump করতে পারলে:

```sql
SELECT * FROM users;
-- TDE দিয়ে: nid_number = "1234567890123"  ← plaintext!
-- FLE দিয়ে: nid_number = "gAAAAABm_FLE_token..."  ← Fernet token!
```

FLE-তে database access পেলেও plaintext দেখা যায় না।
Decrypt করতে application-এর secret key দরকার — vault/env-এ সুরক্ষিত।

---

## ২. Fernet Cryptography

Fernet = AES-128-CBC (গোপনীয়তা) + HMAC-SHA256 (অখণ্ডতা)

বৈশিষ্ট্য:
- Authenticated Encryption — tamper হলে InvalidToken exception
- Timestamp embedded — token age check সম্ভব
- Random IV প্রতিবার — একই plaintext → ভিন্ন ciphertext
- Searchable নয় — WHERE nid_number = ? SQL কাজ করে না

---

## ৩. আর্কিটেকচার

HTTP Request → Router → Service → Repository
                                        ↓
                              EncryptedString TypeDecorator  ← CRYPTOGRAPHIC BOUNDARY
                                        ↓ encrypt / decrypt
                                    Database (Fernet tokens)

নিয়ম: Repository, Service, Router সবসময় plaintext নিয়ে কাজ করে।

---

## ৪. মূল ফাইল: app/core/encryption.py

FernetEngine: Singleton crypto engine
- encrypt(plaintext) → Fernet token
- decrypt(token) → plaintext, InvalidToken হলে → EncryptionTamperingException

EncryptedString TypeDecorator (SQLAlchemy):
- process_bind_param: Python → DB (encrypt on INSERT/UPDATE)
- process_result_value: DB → Python (decrypt on SELECT)
- impl = Text (DB column type)

UserModel তে:
  nid_number: Mapped[str | None] = mapped_column(EncryptedString, nullable=True)

---

## ৫. নতুন Endpoint

PUT /users/{user_id}/nid
Body: {"nid_number": "1234567890123"}
Response: UserResponse (nid_number decrypted)

---

## ৬. RCA — ৪টি Bug

RCA-A: ভুল Import — Pattern #142
  from app.repositories.user_repository import SqlAlchemyUserRepository  ❌
  from app.repositories.sqlalchemy_user_repository import ...             ✅
  lifespan hook-এ ব্যবহৃত class সঠিক module থেকে import করতে হবে।

RCA-B: Generic Exception Code — Pattern #141
  UserNotFoundException.code = "ENTITY_NOT_FOUND"  ❌  (too generic)
  UserNotFoundException.code = "USER_NOT_FOUND"    ✅  (domain-specific)

RCA-C: ভুল Test URL — Pattern #145
  client.put("/api/v1/users/1/nid")  ❌  (prefix নেই)
  client.put("/users/1/nid")         ✅

RCA-D: Cross-Session Raw SQL — Pattern #144
  ASGI transport session ও async_session_factory() session আলাদা।
  HTTP response থেকে data verify করতে হবে, raw SQL নয়।

---

## ৭. টেস্ট ফলাফল

test_field_level_encryption.py  → 12/12 ✅
Full Suite                      → 482 passed, 0 failed ✅

---

## ৮. নতুন SKILL.md Patterns (Day 49)

#138 — FLE + TypeDecorator আর্কিটেকচার
#139 — Fernet key → Pydantic SecretStr
#140 — EncryptionTamperingException for HMAC violation
#141 — Domain-specific exception codes
#142 — Lifespan import path verification
#143 — Nullable FLE fields — None guard
#144 — ASGI session boundary discipline
#145 — Test URL path verification

---

## ৯. Git Commit

git commit -m "feat(day-49): Field-Level Encryption (FLE) with Fernet and SQLAlchemy TypeDecorator"
18 files changed, 957 insertions(+), 16 deletions(-)

---

## ১০. পরবর্তী দিন: Day 50

বিষয়: Current User Dependency — get_current_user, get_current_active_user
JWT token থেকে authenticated user context extract করা।
