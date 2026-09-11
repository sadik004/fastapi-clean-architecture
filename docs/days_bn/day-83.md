# ডে ৮৩: ফিনটেক ডাবল-এন্ট্রি লেজার ডোমেন মডেলিং আর্কিটেকচার (অ্যাকাউন্টস, জার্নাল এন্ট্রিজ, পোস্টিংস এবং জিরো-সাম ব্যালেন্স ইনভেরিয়েন্ট)

## ১. ভূমিকা ও উদ্দেশ্য (Objective & Overview)
আজ আমরা প্রবেশ করলাম আমাদের ১০ দিনের **Phase 8: Capstone Distributed Fintech Double-Entry Ledger System (Days 83–90)**-এ। বিশ্বের শীর্ষস্থানীয় ফিনটেক প্ল্যাটফর্মগুলো (যেমন: Stripe, Square, bKash, Modern Treasury) কীভাবে শত শত কোটি টাকার লেনদেন নির্ভুলভাবে রেকর্ড ও হিসাবরক্ষণ করে, তার মূল ভিত্তি হলো **ডাবল-এন্ট্রি লেজার অ্যাকাউন্টিং (Double-Entry Bookkeeping)**।

আজকের প্রধান শিক্ষণীয় লক্ষ্য:
1. মার্টিন ফাউলারের Accounting Patterns ও আন্তর্জাতিক ব্যাংকিং মানদণ্ড অনুযায়ী ৩টি মূল রিলেশনাল এনটিটি (`LedgerAccountModel`, `JournalEntryModel`, `JournalPostingModel`) ডিজাইন ও মডেলিং করা।
2. **Zero Mutable Balance Column Invariant**: অ্যাকাউন্টে কখনোই কোনো পরিবর্তনযোগ্য `balance` কলাম না রাখা—ব্যালেন্স সর্বদা অপরিবর্তনশীল পোস্টিং লেগের সামারি হিসেবে ডায়নামিকালি হিসাব করা।
3. **Zero-Sum Balance Invariant**: প্রতিটি জার্নাল এন্ট্রিতে কমপক্ষে ২টি লেগ থাকবে এবং $\sum \text{Debits} == \sum \text{Credits}$ নিখুঁতভাবে মিলতে হবে; অমিল হলে লেনদেন বাতিল করে HTTP 422 Unprocessable Entity রিটার্ন করা।
4. **Arbitrary Precision Decimals**: ফ্লোটিং পয়েন্টের বিপজ্জনক ত্রুটি রোধ করতে কঠোরভাবে `Numeric(18, 4)` ও পাইথন `Decimal` ব্যবহার করা।
5. ক্লিন আর্কিটেকচার রুলস (Rules 1–5) অক্ষরে অক্ষরে মেনে রাউটার, সার্ভিস ও রিপোজিটরি লেয়ার সম্পন্ন করা।

---

## ২. 📌 বাস্তব জীবনের ব্যাংকিং/ফিনটেক এনালজি (Real-World Analogy)

ধরা যাক, একজন গ্রাহক **রাকিব** তার ব্যাংক অ্যাকাউন্ট থেকে **bKash** ওয়ালেটে ১,০০০ টাকা লোড করলেন। 

একটি সাধারণ বা অপেশাদার সফটওয়্যারে ডেভেলপাররা হয়তো লিখবেন:
```sql
-- বিপজ্জনক অপেশাদার পদ্ধতি (Single-Entry Slop)
UPDATE users SET balance = balance + 1000 WHERE id = 42;
```
এই কুৎসিত পদ্ধতিতে ৩টি মারাত্মক বিপর্যয় ঘটে:
1. **টাকা এলো কোথা থেকে?** সিস্টেমের কোনো প্রমাণ নেই টাকাটা ব্যাংক থেকে এলো, নাকি হ্যাকার ডেটাবেজে সরাসরি ১,০০০ টাকা বসিয়ে দিল।
2. **রেস কন্ডিশন ও ব্যালেন্স ওভাররাইট**: রাকিব যদি একই মিলি-সেকেন্ডে দুটি লেনদেন করেন, ডেটাবেজের ওভাররাইটে টাকা হারিয়ে যেতে পারে।
3. **অডিট ট্রেইলের অভাব**: বাংলাদেশ ব্যাংক বা এক্সটার্নাল অডিটর আসলে আপনি কোনো প্রমাণ দেখাতে পারবেন না।

### ফিনটেক ডাবল-এন্ট্রি সমাধান:
বাস্তব ব্যাংকিং ব্যবস্থায় টাকা কখনো শূন্য থেকে তৈরি বা ধ্বংস হয় না (Conservation of Money)। এটি সর্বদা এক স্থান থেকে অন্য স্থানে স্থানান্তরিত হয়:
1. **Bank Cash Reserve (ASSET)**: ডেবিট ১,০০০ টাকা (কোম্পানির ব্যাংকে ক্যাশ বৃদ্ধি পেল)।
2. **Customer Wallet (LIABILITY)**: ক্রেডিট ৯৭৫ টাকা (গ্রাহককে কোম্পানি ৯৭৫ টাকা পরিশোধ করতে বাধ্য)।
3. **Processing Fee (REVENUE)**: ক্রেডিট ২৫ টাকা (কোম্পানির প্রসেসিং ফি বাবদ আয়)।

এখানে:
$$\sum \text{Debits} = 1000.0000 = \sum \text{Credits} (975.0000 + 25.0000) = 1000.0000$$
মোট ডেবিট এবং মোট ক্রেডিট নিখুঁতভাবে সমান ($1000 = 1000$), যাকে বলা হয় **Zero-Sum Balance Invariant**।

---

## ৩. আর্কিটেকচারাল ফ্লো ডায়াগ্রাম (Architecture Flowchart)

```mermaid
flowchart TD
    Client[Fintech Client / Checkout Engine] -->|POST /api/v1/ledger/entries| Router[Ledger Router\n(HTTP Transport Layer)]
    
    subgraph Core Validation & Clean Boundaries
        Router -->|JournalEntryCreateDTO| Service[LedgerDomainService\n(Pure Business Rules)]
        Service --> Check1{Postings Count >= 2?}
        Check1 -->|No| Err1[Raise UnbalancedJournalEntryException\nHTTP 422]
        Check1 -->|Yes| Check2{sum Debits == sum Credits?}
        Check2 -->|No| Err2[Raise UnbalancedJournalEntryException\nHTTP 422]
        Check2 -->|Yes| Check3{Accounts Exist & Active?}
        Check3 -->|No| Err3[Raise LedgerAccountNotFoundException\nHTTP 404]
    end

    subgraph Dependency Inversion Layer
        Check3 -->|Yes| Protocol[LedgerRepositoryProtocol]
        Protocol --> RepoImpl[SqlAlchemyLedgerRepository]
    end

    subgraph Append-Only Ledger Persistence
        RepoImpl -->|Atomic DB Transaction| DB[(PostgreSQL / SQLite Database)]
        DB --> Header[(journal_entries\nUUIDv7, ref_id, posted_at)]
        DB --> Postings[(journal_postings\nUUIDv7, account_id, amount, direction)]
    end

    subgraph Dynamic Balance Engine
        Client -->|GET /api/v1/ledger/accounts/:id/balance| Router
        Router --> Service
        Service -->|O 1 Aggregation Query| RepoImpl
        RepoImpl -->|SQL SUM CASE| Agg[total_debits, total_credits]
        Agg --> Formula{Account Type?}
        Formula -->|ASSET / EXPENSE| NormalDebit[Balance = Debits - Credits]
        Formula -->|LIABILITY / EQUITY / REVENUE| NormalCredit[Balance = Credits - Debits]
    end
```

---

## ৪. মূল ডোমেন মডেল ও অ্যাকাউন্টিং ইনভেরিয়েন্ট

### ৪.১ অ্যাকাউন্টের ৫টি মৌলিক প্রকারভেদ (`AccountType`)
ফিন্যান্সিয়াল ডোমেনে প্রতিটি অ্যাকাউন্ট নিম্নোক্ত ৫টির একটিতে বিভক্ত থাকে:
1. **ASSET (সম্পদ)**: যেমন ক্যাশ, ব্যাংক ব্যালেন্স। এর **Normal Balance: DEBIT**। (ডেবিটে ব্যালেন্স বাড়ে, ক্রেডিটে কমে)।
2. **LIABILITY (দায়)**: যেমন গ্রাহকের জমা ওয়ালেট ব্যালেন্স। এর **Normal Balance: CREDIT**। (ক্রেডিটে ব্যালেন্স বাড়ে, ডেবিটে কমে)।
3. **EQUITY (মালিকানাস্বত্ব)**: বিনিয়োগকারীর মূলধন। **Normal Balance: CREDIT**।
4. **REVENUE (আয়)**: ট্রানজ্যাকশন ফি, সাবস্ক্রিপশন ফি। **Normal Balance: CREDIT**।
5. **EXPENSE (ব্যয়)**: সার্ভার খরচ, গেটওয়ে ফি। **Normal Balance: DEBIT**।

### ৪.২ ব্যালেন্স ক্যালকুলেশন ফর্মুলা (Normal Balance Rules)
| অ্যাকাউন্ট টাইপ | নরমাল ব্যালেন্স | ব্যালেন্স গণনার সূত্র |
| :--- | :--- | :--- |
| **ASSET** | DEBIT | $\text{Balance} = \sum \text{Debits} - \sum \text{Credits}$ |
| **EXPENSE** | DEBIT | $\text{Balance} = \sum \text{Debits} - \sum \text{Credits}$ |
| **LIABILITY** | CREDIT | $\text{Balance} = \sum \text{Credits} - \sum \text{Debits}$ |
| **EQUITY** | CREDIT | $\text{Balance} = \sum \text{Credits} - \sum \text{Debits}$ |
| **REVENUE** | CREDIT | $\text{Balance} = \sum \text{Credits} - \sum \text{Debits}$ |

---

## ৫. স্টেপ-বাই-স্টেপ ইমপ্লিমেন্টেশন ব্রেকডাউন

### ধাপ ১: ডেটাবেজ ডোমেন মডেল (`app/models/ledger.py`)
- `LedgerAccountModel`: ইউনিক `account_number`, `name`, `account_type`, `currency`, `is_active`। এতে কোনো `balance` কলাম নেই!
- `JournalEntryModel`: ট্রানজ্যাকশনের প্যারেন্ট হেডার। ইউনিক `reference_id` (Idempotency Key), `description`, `posted_at`।
- `JournalPostingModel`: চাইল্ড পোস্টিং লেগ। `journal_entry_id`, `account_id`, `amount` (`Numeric(18, 4)`), `direction` (`DEBIT` বা `CREDIT`)।

### ধাপ ২: ডাটাবেজ মাইগ্রেশন (`alembic/versions/8bbc80ff6f79_create_double_entry_ledger_tables.py`)
- অ্যালেমবিক মাইগ্রেশন জেনারেট ও এক্সিকিউট করে তিনটি টেবিল ও বি-ট্রি ইনডেক্স তৈরি করা হয়েছে।

### ধাপ ৩: স্কিমা ও ডিটিও (`app/schemas/ledger.py`)
- `AccountType` ও `PostingDirection` ইনাম।
- `LedgerAccountCreate`, `LedgerAccountResponse`।
- `PostingCreateDTO`, `PostingResponseDTO`।
- `JournalEntryCreateDTO`, `JournalEntryResponseDTO`।
- `AccountBalanceResponse`।

### ধাপ ৪: ডোমেন এক্সেপশন ও হ্যান্ডলার (`app/core/exceptions.py`, `app/core/exception_handlers.py`)
- `UnbalancedJournalEntryException`: জিরো-সাম রুল ভঙ্গ হলে HTTP 422 Unprocessable Entity রিটার্ন করে।
- `LedgerAccountNotFoundException`: অ্যাকাউন্ট না পেলে HTTP 404।
- `DuplicateReferenceException`: ডুপ্লিকেট রেফারেন্স আইডি হলে HTTP 409 Conflict।
- `InactiveLedgerAccountException`: নিষ্ক্রিয় অ্যাকাউন্টে পোস্টিং করতে গেলে HTTP 400।

### ধাপ ৫: রিপোজিটরি প্রোটোকল ও কনক্রিট ক্লাস (`app/repositories/ledger_repository.py`)
- `LedgerRepositoryProtocol`: অ্যাবস্ট্রাক্ট প্রোটোকল।
- `SqlAlchemyLedgerRepository`: অপ্টিমাইজড SQL অ্যাগ্রিগেশন কুয়েরি (`func.sum(case(...))`) ব্যবহার করে $\mathcal{O}(1)$ রাউন্ড-ট্রিপে ব্যালেন্স হিসাব করে।
- `InMemoryLedgerRepository`: দ্রুত টেস্ট এক্সিকিউশনের জন্য মেমোরি হ্যাশম্যাপ-ভিত্তিক রিপোজিটরি।

### ধাপ ৬: ডোমেন সার্ভিস (`app/services/ledger_domain_service.py`)
- জিরো-সাম ভ্যালিডেশন: $\sum \text{Debits} == \sum \text{Credits}$।
- অ্যাকাউন্ট অ্যাক্টিভ কিনা ভেরিফিকেশন।
- নরমাল ব্যালেন্সের গাণিতিক হিসাব।

### ধাপ ৭: রাউটার লেয়ার ও অ্যাপ্লিকেশন মাউন্টিং (`app/routers/ledger_router.py`, `app/main.py`)
- RESTful এন্ডপয়েন্ট:
  - `POST /api/v1/ledger/accounts`
  - `GET  /api/v1/ledger/accounts`
  - `GET  /api/v1/ledger/accounts/{account_id}`
  - `GET  /api/v1/ledger/accounts/{account_id}/balance`
  - `POST /api/v1/ledger/entries`
  - `GET  /api/v1/ledger/entries/{entry_id}`

---

## ৬. কোড ডিপ-ডাইভ (Key Code Highlights)

### ডায়নামিক ব্যালেন্স অ্যাগ্রিগেশন কুয়েরি (SQLAlchemy 2.0)
```python
# app/repositories/ledger_repository.py
stmt = select(
    func.coalesce(
        func.sum(
            case(
                (JournalPostingModel.direction == PostingDirection.DEBIT.value, JournalPostingModel.amount),
                else_=Decimal("0.0000"),
            )
        ),
        Decimal("0.0000"),
    ).label("total_debits"),
    func.coalesce(
        func.sum(
            case(
                (JournalPostingModel.direction == PostingDirection.CREDIT.value, JournalPostingModel.amount),
                else_=Decimal("0.0000"),
            )
        ),
        Decimal("0.0000"),
    ).label("total_credits"),
).where(JournalPostingModel.account_id == account_id)
```

### জিরো-সাম ভ্যালিডেশন ইঞ্জিন
```python
# app/services/ledger_domain_service.py
total_debits = Decimal("0.0000")
total_credits = Decimal("0.0000")

for posting in dto.postings:
    if posting.direction == PostingDirection.DEBIT:
        total_debits += posting.amount
    elif posting.direction == PostingDirection.CREDIT:
        total_credits += posting.amount

imbalance = abs(total_debits - total_credits)
if total_debits != total_credits:
    raise UnbalancedJournalEntryException(
        f"Journal entry postings violate zero-sum invariant: debits ({total_debits}) != credits ({total_credits}). Imbalance: {imbalance}",
        total_debits=total_debits,
        total_credits=total_credits,
        imbalance=imbalance,
    )
```

---

## ৭. ⚠️ যে ভুলগুলো প্রোডাকশনে রক্তক্ষরণ ঘটায় (Production Pitfalls)

1. **ফ্লোটিং পয়েন্ট বা ফ্লোট টাইপ ব্যবহার (`float` Slop)**:
   - *ভুল*: মানি অ্যামাউন্টের জন্য `float` বা `REAL` ব্যবহার করা।
   - *রক্তক্ষরণ*: বাইনারি ফ্লোটিং পয়েন্টে `0.1 + 0.2 == 0.30000000000000004` হয়। ১০ লাখ ট্রানজ্যাকশন শেষে হিসাবে কয়েক হাজার টাকার অমিল দেখা দেয়।
   - *প্রতিকার*: ডেটাবেজে `Numeric(18, 4)` এবং পাইথনে `Decimal` টাইপ ব্যবহার করা।

2. **অ্যাকাউন্টে মিউটেবল `balance` কলাম রাখা**:
   - *ভুল*: `ledger_accounts` টেবিলে `balance` কলাম রেখে সরাসরি `UPDATE` করা।
   - *রক্তক্ষরণ*: কনকারেন্ট লেনদেনে রেস কন্ডিশনে ব্যালেন্স ওভাররাইট হয়ে টাকা উধাও হয়ে যায়, এবং কোনো অডিট ট্রেইল থাকে না।
   - *প্রতিকার*: অ্যাকাউন্টে কোনো ব্যালেন্স কলাম রাখা যাবে না। ব্যালেন্স সর্বদা অপরিবর্তনীয় পোস্টিং লেগের যোগফল থেকে হিসাব হবে।

3. **পোস্টিং রেকর্ড ডিলিট বা আপডেট করার অনুমতি দেওয়া**:
   - *ভুল*: কোনো ভুলের কারণে পোস্টিং ডিলিট (`DELETE`) বা আপডেট (`UPDATE`) করা।
   - *রক্তক্ষরণ*: ফাইন্যান্সিয়াল রেগুলেশন ও PCI-DSS নিয়মের লঙ্ঘন।
   - *প্রতিকার*: রিপোজিটরি ও ডেটাবেজে পোস্টিং সম্পূর্ণ **Append-Only** (Immutable)। ভুল লেনদেন ঠিক করতে হলে বিপরীতমুখী নতুন অ্যাডজাস্টমেন্ট জার্নাল এন্ট্রি পোস্ট করতে হয়।

---

## ৮. 💡 ইন্টারভিউ প্রস্তুতি ও প্রশ্নোত্তর (Interview Q&A)

### প্রশ্ন ১: ব্যাংকিং সিস্টেমে অ্যাকাউন্টে সরাসরি `balance` কলাম না রেখে ডাবল-এন্ট্রি লেজার কেন ব্যবহার করা হয়?
**উত্তর**: সরাসরি `balance` কলামে `UPDATE` চালালে ৩টি বড় সমস্যা হয়: রেস কন্ডিশন, হিস্টোরিক্যাল অডিট ট্রেইল হারিয়ে যাওয়া এবং টাকা কোথা থেকে এলো বা কোথায় গেল তা প্রমাণ করতে না পারা। ডাবল-এন্ট্রি লেজারে প্রতিটি টাকা এক অ্যাকাউন্ট থেকে ডেবিট হয়ে অন্য অ্যাকাউন্টে ক্রেডিট হয়, যা গাণিতিকভাবে জিরো-সাম নিশ্চিত করে এবং সম্পূর্ণ স্বচ্ছ অডিটাবিলিটি প্রদান করে।

### প্রশ্ন ২: ASSET অ্যাকাউন্টের সাথে LIABILITY অ্যাকাউন্টের ব্যালেন্স গণনার পার্থক্য কী?
**উত্তর**: ASSET ও EXPENSE অ্যাকাউন্টের নরমাল ব্যালেন্স হলো **DEBIT** ($\text{Balance} = \text{Debits} - \text{Credits}$)। অপরদিকে LIABILITY, EQUITY এবং REVENUE অ্যাকাউন্টের নরমাল ব্যালেন্স হলো **CREDIT** ($\text{Balance} = \text{Credits} - \text{Debits}$)।

### প্রশ্ন ৩: একটি জার্নাল এন্ট্রিতে কি ২টির বেশি পোস্টিং লেগ থাকতে পারে?
**উত্তর**: হ্যাঁ! একে **কম্পাউন্ড জার্নাল এন্ট্রি (Compound Journal Entry)** বলা হয়। যেমন গ্রাহক ডিপোজিট করার সময়: ব্যাংকে জমা (Asset) +$১,০০০ (DEBIT), কাস্টমার ওয়ালেটে (Liability) +$৯৭৫ (CREDIT), এবং ফি বাবদ (Revenue) +$২৫ (CREDIT)। এখানে ৩টি লেগ থাকলেও মোট ডেবিট ($১,০০০) = মোট ক্রেডিট ($১,০০০)।

---

## ৯. ভেরিফিকেশন ও টেস্ট রেজাল্ট (Verification)
আমরা ৭টি সমন্বিত টেস্ট কেস সম্পন্ন করেছি:
1. `test_ledger_account_creation_and_balance_flow`: অ্যাকাউন্ট তৈরি, পোস্টিং এবং ডায়নামিক ব্যালেন্স চেক।
2. `test_unbalanced_journal_entry_rejected_with_422`: অসম পোস্টিং ৪২২ এরর দিয়ে বাতিল।
3. `test_duplicate_reference_id_rejected_with_409`: আইডেমপোটেন্সি কি দিয়ে ডুপ্লিকেট এন্ট্রি ৪০৯ কনফ্লিক্ট।
4. `test_compound_multi_leg_journal_entry`: ৩-লেগ বিশিষ্ট ডিপোজিট ও ফি লেনদেন।
5. `test_arbitrary_precision_fractional_decimals`: সাব-সেন্ট `0.0001` নির্ভুলতা পরীক্ষা।
6. `test_nonexistent_account_returns_404`: অস্তিত্বহীন অ্যাকাউন্টে পোস্টিং বাতিল।
7. `test_in_memory_ledger_service_unit_test`: ইন-মেমোরি রিপোজিটরির মাধ্যমে সার্ভিস লেয়ার টেস্ট।

```text
======================== 7 passed, 1 warning in 1.04s =========================
================= Architecture Compliance: 10/10 PASSED ====================
================== Mypy Strict: 0 Issues in 11 files ========================
```

---

## ১০. রিক্যাপ ও পরবর্তী দিনের পূর্বাভাস (Next Steps)
- **আজ সম্পন্ন হলো**: Phase 8 এর প্রথম দিন (Day 83)—ডাবল-এন্ট্রি লেজার ডোমেন মডেলিং, ডায়নামিক ব্যালেন্স এবং জিরো-সাম ইনভেরিয়েন্ট।
- **পরবর্তী দিন (Day 84)**: **Distributed Idempotency & Financial Transaction Deduplication Architecture** (রেডিস ডিস্ট্রিবিউটেড লক, পে-লোড হ্যাশিং ও কনকারেন্সি রেস প্রোটেকশন)।
