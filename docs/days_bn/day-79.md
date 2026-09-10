# ডে ৭৯: স্ট্যাটিক সিকিউরিটি অডিট ও কোড হার্ডেনিং আর্কিটেকচার (AST Security Analysis with Bandit & Semgrep)

---

### ১. আমরা কী বানিয়েছি? (What did we build?)
আমরা একটি এন্টারপ্রাইজ-গ্রেড স্ট্যাটিক অ্যাপ্লিকেশন সিকিউরিটি টেস্টিং (SAST - Static Application Security Testing) এবং অ্যাবস্ট্রাক্ট সিনট্যাক্স ট্রি (AST - Abstract Syntax Tree) কোড হার্ডেনিং আর্কিটেকচার তৈরি করেছি। এই আর্কিটেকচারটি কোড রান না করেই সরাসরি পাইথন সোর্স কোডের সিনট্যাক্স ট্রি পার্স করে ওভ্যাস্প (OWASP Top 10) নিরাপত্তা দুর্বলতা (SQL Injection, Hardcoded Secrets, Insecure PRNG, Code Injection, Unsafe Deserialization) শনাক্ত করে এবং আমাদের ৪টি বাধ্যতামূলক আর্কিটেকচারাল রুল এনফোর্স করে:

1. **স্বয়ংক্রিয় সিকিউরিটি অডিট কনফিগারেশন (`.bandit`, `.semgrep.yml`, `deployments/security/semgrep_rules.yml`)**:
   - ব্যানডিট (`Bandit`) এর মাধ্যমে সমগ্র `app/` ডিরেক্টরি অডিট করা, যেখানে টেস্ট ও মাইগ্রেশন ফাইল বাদ দিয়ে কঠোরভাবে জিরো হাই ও জিরো মিডিয়াম কনফিডেন্স ভায়োলেশন নিশ্চিত করা হয়।
   - সেমগ্রেপ (`Semgrep`) পলিসির মাধ্যমে এন্টারপ্রাইজ আর্কিটেকচারাল রুল এনফোর্স করা।
2. **৪টি বাধ্যতামূলক আর্কিটেকচারাল রুল (Core Security & Architectural Invariants)**:
   - **রুল ১: রাউটারে কোনো র এসকিউএল নিষিদ্ধ (No Raw SQL in Routers - CWE-89)**: `app/routers/` এর ভেতরে সরাসরি `session.execute(text(...))` কল সম্পূর্ণ নিষিদ্ধ। ডাটাবেসের সমস্ত কোয়েরি অবশ্যই ৩-টিয়ার ক্লিন আর্কিটেকচারের রিপোজিটরি লেয়ারের (`app/repositories/`) মাধ্যমে সম্পন্ন করতে হবে।
   - **রুল ২: ইনসিকিউর র‍্যান্ডম নিষিদ্ধ (No Insecure PRNG - CWE-338)**: সিকিউরিটি টোকেন, ওটিপি বা সেশনের জন্য পাইথনের ডিফল্ট `random` মডিউল ব্যবহার নিষিদ্ধ; ক্রিপ্টোগ্রাফিক সুরক্ষায় স্ট্যান্ডার্ড লাইব্রেরির `secrets` মডিউল বাধ্যতামূলক (একমাত্র রেসিলিয়েন্স ব্যাকঅফ জিটার ব্যতীত)।
   - **রুল ৩: প্রোডাকশনে `print()` নিষিদ্ধ (No Print in Production - CWE-778)**: সরাসরি আনস্ট্রাকচার্ড `print()` মেমোরি লিক ও লগ পাইপলাইন অচল করে; তাই শুধুমাত্র `structlog` স্ট্রাকচার্ড JSON লগিং এনফোর্স করা হয়েছে।
   - **রুল ৪: বিপজ্জনক কোড এক্সিকিউশন ও হার্ডকোডেড সিক্রেট নিষিদ্ধ (CWE-94, CWE-502, CWE-798)**: `eval()`, `exec()`, `pickle.loads()` এবং ভ্যারিয়েবলে প্লেইনটেক্সট লাইভ এপিআই কি/সিক্রেট হার্ডকোড করা চিরতরে নিষিদ্ধ।
3. **প্রোগ্রাম্যাটিক AST সিকিউরিটি সার্ভিস (`app/services/security_audit_service.py`)**:
   - $\mathcal{O}(N_{\text{nodes}})$ লিনিয়ার টাইম কমপ্লেক্সিটিতে পাইথনের বিল্ট-ইন `ast.NodeVisitor` ব্যবহার করে কোডের প্রতিটি সিনট্যাক্স নোড পরীক্ষা করা।
   - সাবপ্রসেসের মাধ্যমে আইসোলেটেড ব্যানডিট স্ক্যানার রান করে কমপ্লায়েন্স রিপোর্ট তৈরি করা।
4. **অবজারভেবিলিটি ও কমপ্লায়েন্স এপিআই (`app/routers/security_audit_router.py`)**:
   - `GET /observability/security/audit-summary`: রিয়েল-টাইমে কোডবেসের সিকিউরিটি স্ট্যাটাস (`SECURE` বনাম `VULNERABLE`) ও ইস্যু কাউন্ট রিটার্ন করে।
   - `GET /observability/security/audit-details`: প্রতিটি ইস্যুর ফাইল পাথ, লাইন নাম্বার, তীব্রতা এবং CWE রেফারেন্স প্রদান করে।

---

### ২. 📌 ব্যবহৃত DSA ও সাইবার-সিকিউরিটি আর্কিটেকচার প্যাটার্নের নাম
- **অ্যাবস্ট্রাক্ট সিনট্যাক্স ট্রি ট্রাভার্সাল (Abstract Syntax Tree Traversal — $\mathcal{O}(N_{\text{nodes}})$ Linear Recursive Visitor Pattern via `ast.NodeVisitor`)**
- **স্ট্যাটিক অ্যাপ্লিকেশন সিকিউরিটি টেস্টিং (Static Application Security Testing - SAST Code Hardening & Semantic Rule Matching)**
- **৩-টিয়ার আর্কিটেকচারাল বাউন্ডারি গেটকিপিং (Layered Clean Architecture Boundary Enforcement — In-Router Raw SQL Elimination)**
- **ক্রিপ্টোগ্রাফিক এন্ট্রপি ভ্যালিডেশন (Cryptographically Secure Pseudo-Random Number Generation - CSPRNG via `secrets`)**
- **ইন-মেমরি সিম্বলিক সিক্রেট স্ক্যানিং (Entropy-based & Identifier-Pattern Heuristic Secret Scanning)**

---

### ৩. 🚀 প্রোডাকশনে ঠিক কখন ব্যবহার করব? (When to use in Production)
- **সিআই/সিডি পাইপলাইনের কোয়ালিটি গেটে (CI/CD Pull Request Gates):** কোনো ইঞ্জিনিয়ার যখন গিটহাবে পিআর (Pull Request) পাঠাবে, অটোমেটিক গিটহাব অ্যাকশনস ব্যানডিট এবং সেমগ্রেপ রান করবে। যদি একটিও High বা Medium সিকিউরিটি ইস্যু থাকে, পিআর মার্জ স্বয়ংক্রিয়ভাবে ব্লক হয়ে যাবে।
- **প্রি-কমিট হুকে (Local Pre-Commit Hooks):** কোড গিট রিপোজিটরিতে কমিট করার আগেই লোকাল মেশিনে ডেভেলপারকে সতর্ক করার জন্য।
- **সিকিউরিটি কমপ্লায়েন্স ও অডিট রিপোর্টে (SOC2 / ISO 27001 Auditing):** এন্টারপ্রাইজ গ্রাহকদের জন্য সফটওয়্যারের সোর্স কোডে যে কোনো হার্ডকোডেড পাসওয়ার্ড বা ইনজেকশন ভলনারেবিলিটি নেই তার অটোমেটেড প্রুফ-অফ-কমপ্লায়েন্স ড্যাশবোর্ড প্রদর্শনে।

---

### ৪. 🎯 কী কারণে বা কোন পরিস্থিতিতে ব্যবহার করব? (Why to use / Technical Triggers)
- **ম্যানুয়াল কোড রিভিউয়ের সীমাবদ্ধতা দূরীকরণ:** মানুষ ক্লান্ত হতে পারে বা অসাবধানতাবশত একটি `session.execute(text(f"SELECT * FROM users WHERE id = {user_id}"))` এড়িয়ে যেতে পারে। অটোমেটেড AST স্ক্যানার কখনো ভুল করে না।
- **৩-টিয়ার আর্কিটেকচার লঙ্ঘন রোধ:** জুনিয়র ইঞ্জিনিয়াররা অনেক সময় শর্টকাট মারার জন্য রাউটারের ভেতরে সরাসরি র ডাটাবেস কোয়েরি লিখে ফেলে। এতে ডাটাবেস রেপ্লিকা রাউটিং নষ্ট হয় এবং কোড মনোলিথিক জগাখিচুড়িতে পরিণত হয়।
- **ক্রিপ্টোগ্রাফিক পূর্বাভাসযোগ্যতা প্রতিরোধ:** পাইথনের ডিফল্ট `random` মডিউল মার্সেন টুইস্টার (Mersenne Twister) অ্যালগরিদম ব্যবহার করে। আক্রমণকারী মাত্র ৬২৪টি সংখ্যা দেখে পরবর্তী সমস্ত টোকেন হুবহু প্রেডিক্ট করে অ্যাকাউন্ট হাইজ্যাক করতে পারে।

---

### ৫. বাস্তব জীবনের গল্প ও উপমা (Airport Baggage Security X-Ray Scanner Analogy)
আন্তর্জাতিক বিমানবন্দরের সিকিউরিটি চেকিংয়ের কথা চিন্তা করুন:

যাত্রীরা যখন বিমানে ওঠার জন্য সিকিউরিটি গেটে আসে, তখন তাদের সাথে থাকা সমস্ত লাগেজ একটি কনভেয়ার বেল্টের মাধ্যমে শক্তিশালী **এক্স-রে স্ক্যানার (X-Ray Scanner)** মেশিনের ভেতর দিয়ে যায়।
- **আনাড়ি এয়ারপোর্ট (কোনো SAST ছাড়া):** যাত্রীদের কোনো ব্যাগ স্ক্যান করা হলো না। প্লেন আকাশে ওড়ার পর যদি কোনো যাত্রী বোমা বা ধারালো অস্ত্র বের করে ফেলে, তখন আকাশে পাইলট বা ক্রুদের কিছুই করার থাকে না—বিপর্যয় নিশ্চিত! (এটি হলো সরাসরি আন-অডিটেড কোড প্রোডাকশনে ডেপ্লয় করা)।
- **টেক্সট রেজাক্স সার্চ (সাধারণ মেটাল ডিটেক্টর):** দরজায় দাঁড়ানো দারোয়ান একটি হ্যান্ডহেল্ড মেটাল ডিটেক্টর দিয়ে যাত্রীকে বাইরে থেকে ছুঁয়ে দেখছে। কিন্তু ব্যাগের ভেতর বইয়ের ভাঁজে বা প্লাস্টিকের প্যাকেটের ভেতর ড্রাগস বা এক্সপ্লোসিভ লুকানো থাকলে মেটাল ডিটেক্টর তা ধরতে পারে না (রেগুলার এক্সপ্রেশন পাইথনের সিনট্যাক্স স্কোপ ও কন্টেক্সট বোঝে না)।
- **আধুনিক সিটি এক্স-রে ও ৩ডি টমোগ্রাফি স্ক্যানার (আমাদের SAST & AST Visitor):**
  1. লাগেজকে খোলা ছাড়াই ৩ডি অ্যাবস্ট্রাক্ট সিনট্যাক্স ট্রির মতো স্তরে স্তরে ভাগ করে স্ক্যান করা হয়।
  2. স্ক্যানার জানে কোন আকৃতির বস্তু ক্ষতিকর (যেমন: `eval()` বা `session.execute(text(...))` দেখলে অ্যালার্ম বেজে ওঠে)।
  3. স্ক্যানার অর্গানিক ও মেটাল উপাদান আলাদা করতে পারে (যেমন: সাধারণ গণিতের `backoff.py` জিটার অনুমোদিত, কিন্তু `auth_service` এ `random` দেখা মাত্রই কনভেয়ার বেল্ট থামিয়ে দেয়)।
  4. অবৈধ বস্তু ধরা পড়লে ব্যাগটিকে আলাদা সিকিউরিটি খাঁচায় ফেলে দেওয়া হয় এবং যাত্রীকে বিমানে চড়তে দেওয়া হয় না (সিআই/সিডি পাইপলাইনে বিল্ড ফেইল করানো)।

আমাদের `SecurityAuditService` এবং সেমগ্রেপ পলিসি হলো সফটওয়্যার ডেলিভারির এই দুর্ভেদ্য এয়ারপোর্ট এক্স-রে স্ক্যানার!

---

### ৬. এটা না বানালে কী মহাবিপদ হতো? (The Disaster Scenario)
ধরা যাক, আমাদের কোম্পানির একজন ডেভেলপার একটি ক্যাশ রিফান্ড রাউটার লিখতে গিয়ে তাড়াহুড়ো করে লিখে ফেলল:
```python
@router.post("/refund")
async def process_refund(order_id: str, db: AsyncSession = Depends(get_db)):
    query = text(f"UPDATE orders SET status = 'REFUNDED' WHERE id = '{order_id}'")
    await db.execute(query)
    await db.commit()
```
এবং সে সিকিউরিটি টোকেন জেনারেট করার জন্য লিখল:
```python
reset_token = str(random.randint(100000, 999999))
```
যদি আমাদের এই স্ট্যাটিক সিকিউরিটি আর্কিটেকচার না থাকত:
1. **ভয়াবহ এসকিউএল ইনজেকশন (SQLi Breach):** একজন আক্রমণকারী `order_id` প্যারামিটারে `' OR '1'='1'; DROP TABLE users; --` পাঠিয়ে ডাটাবেসের সমস্ত গ্রাহকের তথ্য নিমেষেই মুছে বা চুরি করে নিতে পারত।
2. **অ্যাকাউন্ট টেকওভার (Account Takeover):** `random.randint` ক্রিপ্টোগ্রাফিক্যালি সেফ না হওয়ায় আক্রমণকারী সার্ভারের তৈরি কয়েকটি টোকেন পর্যবেক্ষণ করে অ্যাডমিনের পাসওয়ার্ড রিসেট টোকেন বের করে পুরো সার্ভারের নিয়ন্ত্রণ নিয়ে নিত।
3. **বিশাল জরিমানা ও কোম্পানির পতন:** গ্রাহকদের লাখ লাখ ক্রেডিট কার্ড ও এনআইডি নম্বর ডার্ক ওয়েবে ফাঁস হয়ে যেত। জিডিপিআর (GDPR) ও পিসিআই-ডিএসএস (PCI-DSS) অডিটে ব্যর্থ হয়ে কোম্পানিকে কোটি কোটি টাকা জরিমানা গুনতে হতো।

---

### ৭. আমাদের প্রজেক্টের আসল কোড ও কোডের লাইন-বাই-লাইন সহজ ব্যাখ্যা

#### (ক) এএসটি সিকিউরিটি ভিজিটর (`app/services/security_audit_service.py`)
```python
class ASTArchitecturalSecurityVisitor(ast.NodeVisitor):
    """Abstract Syntax Tree visitor scanning Python source nodes in O(N_nodes) time."""

    def __init__(self, filename: str, is_router: bool = False) -> None:
        self.filename: str = filename
        self.is_router: bool = is_router
        self.issues: list[SecurityIssue] = []

    def visit_Call(self, node: ast.Call) -> None:
        # রুল ৪: eval ও exec এর মতো বিপজ্জনক ফাংশন কল ব্লক করা (CWE-94)
        if isinstance(node.func, ast.Name) and node.func.id in ("eval", "exec"):
            self.issues.append(
                SecurityIssue(
                    rule_id="fastapi-no-eval-exec",
                    severity="HIGH",
                    confidence="HIGH",
                    cwe="CWE-94: Improper Control of Generation of Code ('Code Injection')",
                    description=f"Use of dangerous built-in '{node.func.id}()' allows arbitrary code execution.",
                    filename=self.filename,
                    line_number=node.lineno,
                )
            )

        # রুল ৩: প্রোডাকশনে print() নিষিদ্ধ করা (CWE-778)
        elif isinstance(node.func, ast.Name) and node.func.id == "print":
            self.issues.append(
                SecurityIssue(
                    rule_id="fastapi-no-print-in-production",
                    severity="LOW",
                    confidence="HIGH",
                    cwe="CWE-778: Insufficient Logging",
                    description="Direct print() statement detected. Use structlog structured logging instead.",
                    filename=self.filename,
                    line_number=node.lineno,
                )
            )

        elif isinstance(node.func, ast.Attribute):
            # রুল ২: দুর্বল র্যান্ডম PRNG শনাক্তকরণ (CWE-338)
            if isinstance(node.func.value, ast.Name) and node.func.value.id == "random":
                if not self.filename.endswith("backoff.py"):
                    if node.func.attr in ("random", "choice", "randint", "randrange", "uniform"):
                        self.issues.append(
                            SecurityIssue(
                                rule_id="fastapi-no-insecure-random",
                                severity="MEDIUM",
                                confidence="HIGH",
                                cwe="CWE-338: Use of Cryptographically Weak Pseudo-Random Number Generator (PRNG)",
                                description=(
                                    f"Insecure PRNG 'random.{node.func.attr}()' detected. "
                                    "Use standard library 'secrets' module for cryptographically secure values."
                                ),
                                filename=self.filename,
                                line_number=node.lineno,
                            )
                        )

            # রুল ১: রাউটারে র এসকিউএল নিষিদ্ধকরণ (CWE-89)
            if self.is_router and node.func.attr == "execute":
                for arg in node.args:
                    if (
                        isinstance(arg, ast.Call)
                        and isinstance(arg.func, ast.Name)
                        and arg.func.id == "text"
                    ):
                        self.issues.append(
                            SecurityIssue(
                                rule_id="fastapi-no-raw-sql-in-routers",
                                severity="HIGH",
                                confidence="HIGH",
                                cwe="CWE-89: SQL Injection / Architectural Boundary Violation",
                                description=(
                                    "Direct execute(text(...)) raw SQL invocation inside router. "
                                    "Delegate all database operations to the repository layer."
                                ),
                                filename=self.filename,
                                line_number=node.lineno,
                            )
                        )

        self.generic_visit(node)
```

**লাইন-বাই-লাইন ব্যবচ্ছেদ:**
1. `class ASTArchitecturalSecurityVisitor(ast.NodeVisitor)`: পাইথনের স্ট্যান্ডার্ড লাইব্রেরির AST ভিজিটরকে ইনহেরিট করা হয়েছে।
2. `visit_Call(self, node: ast.Call)`: কোডের ভেতরে যত ফাংশন বা মেথড কল আছে (`foo()`), পাইথনের পার্সার সেগুলোকে এই মেথডে পাঠায়।
3. `node.func.id in ("eval", "exec")`: যদি কলের নামটি `eval` বা `exec` হয়, তাৎক্ষণিকভাবে High severity issue যোগ করা হয়।
4. `node.func.id == "print"`: যদি কোনো ডেভেলপার ডিবাগ করার জন্য `print()` রেখে দেয়, তা Low severity আর্কিটেকচারাল ভায়োলেশন হিসেবে নথিভুক্ত হয়।
5. `not self.filename.endswith("backoff.py")`: এক্সপোনেনশিয়াল ব্যাকঅফ নেটওয়ার্ক জিটার ক্রিপ্টোগ্রাফিক বিষয় না হওয়ায় সেটিকে সেমগ্রেপ রুলের মতোই সেফ এক্সেম্পশন দেওয়া হয়েছে।
6. `self.is_router and node.func.attr == "execute"`: ফাইলটি যদি `routers/` ফোল্ডারে থাকে এবং ফাংশন কলটি যদি `execute(text(...))` হয়, এটি ৩-টিয়ার আর্কিটেকচার ভায়োলেশন হিসেবে হাই সিকিউরিটি এলার্ট দেয়।
7. `self.generic_visit(node)`: কারেন্ট নোডের চাইল্ড নোডগুলো রিকার্সিভলি ভিজিট করার জন্য কল করা হয়, যা নিশ্চিত করে কোনো নেস্টেড কল মিস হবে না।

---

#### (খ) সেমগ্রেপ ডিক্লারেটিভ পলিসি (`.semgrep.yml`)
```yaml
rules:
  - id: fastapi-no-raw-sql-in-routers
    patterns:
      - pattern: $SESSION.execute(text(...))
    paths:
      include:
        - "**/app/routers/**"
    message: "Direct execute(text(...)) raw SQL execution inside router violates 3-tier clean architecture boundary (CWE-89). Delegate to repository layer."
    languages: [python]
    severity: ERROR

  - id: fastapi-no-insecure-random
    patterns:
      - pattern-either:
          - pattern: random.random(...)
          - pattern: random.randint(...)
          - pattern: random.choice(...)
    paths:
      exclude:
        - "**/app/core/resilience/backoff.py"
    message: "Use of insecure PRNG 'random' in production code (CWE-338). Use standard library 'secrets' module."
    languages: [python]
    severity: WARNING
```

---

#### (গ) সিকিউরিটি অবজারভার রাউটার (`app/routers/security_audit_router.py`)
```python
@router.get(
    "/audit-summary",
    response_model=SecurityAuditSummaryResponse,
    status_code=status.HTTP_200_OK,
    summary="Get SAST and AST Security Compliance Summary",
)
def get_audit_summary(
    target_dir: Annotated[str, Query(description="Target directory to audit (e.g. app)")] = "app",
    audit_service: SecurityAuditService = Depends(get_security_audit_service),
) -> dict[str, Any]:
    """Execute security scan and return aggregated summary posture."""
    report = audit_service.get_security_summary(target_dir=target_dir)
    return {
        "status": report.status,
        "total_files_scanned": report.total_files_scanned,
        "total_lines_scanned": report.total_lines_scanned,
        "high_severity_count": report.high_severity_count,
        "medium_severity_count": report.medium_severity_count,
        "low_severity_count": report.low_severity_count,
        "ast_violations_count": report.ast_violations_count,
        "scanned_at": report.scanned_at,
    }
```
এটি যেকোনো মনিটরিং ড্যাশবোর্ড বা কুবারনেটিস অপারেটরের মাধ্যমে কল করে ইনস্ট্যান্টলি ভেরিফাই করা যায় যে কোডবেসের স্ট্যাটাস `SECURE` নাকি `VULNERABLE`।

---

### ৮. ভেরিফিকেশন ও টেস্ট সুইট বিশ্লেষণ
আমরা `tests/test_static_security_audit.py` এ ১০টি নিখুঁত টেস্ট তৈরি করেছি যা ১০০% পাস করেছে:
1. `test_bandit_zero_high_medium_in_app`: প্রমাণ করে সমগ্র `app/` ফোল্ডারে ব্যানডিট দিয়ে স্ক্যান করলে ০টি হাই এবং ০টি মিডিয়াম ইস্যু পাওয়া যায়।
2. `test_ast_rule_catches_raw_sql_in_router`: একটি ডামি রাউটার কোড ইনপুট দিয়ে যাচাই করে যে `execute(text(...))` ধরা পড়ে।
3. `test_ast_rule_permits_raw_sql_in_repositories`: যাচাই করে যে রিপোজিটরি ফোল্ডারে জটিল প্রয়োজনে `text()` কুয়েরি অনুমোদন পায় (Clean Architecture compliance)।
4. `test_ast_rule_catches_insecure_random`: ক্রিপ্টোগ্রাফিক্যালি অনিরাপদ `random.randint` শনাক্তকরণ নিশ্চিত করে।
5. `test_ast_rule_catches_eval_exec`: ডাইনামিক কোড ইনজেকশন ভলনারেবিলিটি ধরা নিশ্চিত করে।
6. `test_ast_rule_catches_print`: সাধারণ `print()` স্টেটমেন্ট ক্যাচ করে।
7. `test_ast_rule_catches_hardcoded_secrets`: প্লেইনটেক্সট এপিআই টোকেন অ্যাসাইনমেন্ট ব্লক করে।
8. `test_clean_codebase_zero_ast_violations`: বর্তমান প্রোডাকশন কোডবেস স্ক্যান করে নিশ্চিত করে যে কোনো AST ভায়োলেশন অবশিষ্ট নেই।
9. `test_security_audit_summary_api`: এপিআই এন্ডপয়েন্টে কল করে স্ট্যাটাস `SECURE` প্রাপ্তি নিশ্চিত করে।
10. `test_security_audit_details_api`: বিস্তারিত অডিট রেসপন্সে ভ্যালিড ইস্যু স্কিমা যাচাই করে।

---

### ৯. প্রোডাকশন ট্রাবলশুটিং ও বেস্ট প্র্যাকটিস
1. **ফলস পজিটিভ হ্যান্ডলিং (#nosec Discipline):**
   - ব্যানডিট যদি এমন কোনো লাইনে ফ্ল্যাগ করে যা ইতোমধ্যে রেগুলার এক্সপ্রেশন বা টাইপ সিস্টেম দিয়ে সম্পূর্ণ নিরাপদ (যেমন: `partition_service.py` তে টেবিলের নাম কঠোরভাবে স্যানিটাইজ করা), তখন অন্ধের মতো রুল বন্ধ না করে সুনির্দিষ্টভাবে `# nosec B608` কমেন্ট ব্যবহার করুন।
2. **ফাইল পাথ এক্সক্লুশন (Semgrep Unanchored Globs):**
   - সেমগ্রেপে পাথ লেখার সময় কখনো রিলেটিভ `app/routers/**` লিখবেন না; সর্বদা আন-অ্যাঙ্করড `**/app/routers/**` ব্যবহার করুন, যাতে রুট ডিরেক্টরির বাইরে থেকে স্ক্যান চালালেও রুল সক্রিয় থাকে।
3. **কনফিগারেশন ফাইল থেকে সিক্রেট এক্সক্লুশন:**
   - পাইড্যান্টিক সেটিংসের ডিফল্ট ভ্যালু (যেমন `app/core/config.py` তে টেস্টিং সিক্রেট) যাতে ফলস এলার্ট তৈরি না করে, সেজন্য সেমগ্রেপে সেই সুনির্দিষ্ট ফাইলকে এক্সক্লুড রাখুন।

---

### ১০. ইন্টারভিউ প্রশ্ন ও উত্তর (Architectural Interview Questions)

#### প্রশ্ন ১: রেগুলার এক্সপ্রেশন (RegEx) থাকতে কেন আমরা AST (Abstract Syntax Tree) ভিত্তিক সিকিউরিটি স্ক্যানিং ব্যবহার করি?
**উত্তর:** 
রেগুলার এক্সপ্রেশন হলো শুধুমাত্র টেক্সট প্যাটার্ন ম্যাচিং টুল; এটি প্রোগ্রামিং ভাষার গ্রামার, ভ্যারিয়েবল স্কোপ, ডাটা টাইপ বা কন্টেক্সট বোঝে না। 
উদাহরণস্বরূপ, যদি কমেন্টের ভেতরে লেখা থাকে `# remember not to use eval()`, রেজাক্স সেটিকে আসল কোড ভেবে ফলস পজিটিভ অ্যালার্ম দেবে। আবার যদি কোনো ভ্যারিয়েবলের নাম `eval_score` হয়, রেজাক্স সেটিকে ফাংশন কল মনে করতে পারে। 
অন্যদিকে, পাইথনের `ast` মডিউল কোডকে টোকেনাইজ করে একটি সিনট্যাক্স ট্রি তৈরি করে যেখানে প্রতিটি ফাংশন কল (`ast.Call`), ভ্যারিয়েবল অ্যাসাইনমেন্ট (`ast.Assign`) এবং কন্ট্রোল ফ্লো আলাদা নোড হিসেবে থাকে। AST স্ক্যানিং $\mathcal{O}(N_{\text{nodes}})$ টাইমে নিখুঁতভাবে নির্ধারণ করতে পারে যে একটি এক্সপ্রেশন কমেন্ট, স্ট্রিং লিটারেল নাকি এক্সিকিউটেবল ফাংশন কল। ফলে ফলস পজিটিভ শূন্যের কোঠায় নেমে আসে।

#### প্রশ্ন ২: সিআই/সিডি পাইপলাইনে স্ট্যাটিক সিকিউরিটি টেস্টিং (SAST) এবং ডাইনামিক সিকিউরিটি টেস্টিংয়ের (DAST) মধ্যে মৌলিক পার্থক্য কী এবং কেন দুটোই প্রয়োজন?
**উত্তর:**
- **SAST (White-Box Testing):** কোড রান না করেই সোর্স কোড বা বাইটকোডের সিনট্যাক্স বিশ্লেষণ করে। এটি ডেভেলপমেন্টের শুরুর দিকেই (Shift-Left) ইনজেকশন পয়েন্ট, হার্ডকোডেড ক্রেডেনশিয়াল এবং আর্কিটেকচারাল রুল ভায়োলেশন সুনির্দিষ্ট ফাইল এবং লাইন নাম্বার সহ ধরিয়ে দিতে পারে। ব্যানডিট ও সেমগ্রেপ হলো SAST টুল।
- **DAST (Black-Box Testing):** অ্যাপ্লিকেশন রান করার পর বাইরে থেকে রানিং কন্টেইনারে এইচটিটিপি রিকোয়েস্ট (OWASP ZAP) পাঠিয়ে রানটাইম দুর্বলতা, মিসকনফিগারেশন এবং নেটওয়ার্ক লেভেলের লিক পরীক্ষা করে। এটি ভেতরের সোর্স কোড দেখতে পায় না।
**কেন দুটোই প্রয়োজন?** SAST রানটাইম এনভায়রনমেন্টের সমস্যা (যেমন: ভুল কুবারনেটিস আরবিক পারমিশন বা টিএলএস সার্টিফিকেট মিসম্যাচ) ধরতে পারে না। আবার DAST কোডের ভেতরের আর্কিটেকচারাল লঙ্ঘন (যেমন: রাউটারে র এসকিউএল কল বা মেমোরিতে `print()` থাকা) ধরতে পারে না। তাই প্রোডাকশন-গ্রেড এন্টারপ্রাইজ সিস্টেমে ডিফেন্স-ইন-ডেপথ নিশ্চিত করতে দুটোই একসাথে পরিচালনা করা হয়।
