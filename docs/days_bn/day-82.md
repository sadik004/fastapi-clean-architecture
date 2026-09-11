# ডে ৮২: গিটহাব অ্যাকশনস দিয়ে প্রোডাকশন সিআই/সিডি পাইপলাইন ও মাল্টি-স্টেজ কোয়ালিটি গেটওয়ে (GitHub Actions CI/CD Automation Pipeline)

## ১. আমরা কী বানিয়েছি? (What did we build?)
আমরা আমাদের এন্টারপ্রাইজ ফাস্টএপিআই (FastAPI) ক্লিন আর্কিটেকচার অ্যাপ্লিকেশনের জন্য একটি স্বয়ংক্রিয় ও কঠোর **প্রোডাকশন সিআই/সিডি (Continuous Integration / Continuous Deployment) পাইপলাইন** বানিয়েছি। এটি গিটহাব অ্যাকশনস (`.github/workflows/ci.yml`) এবং একটি লোকাল রানার স্ক্রিপ্ট (`scripts/run_ci_locally.sh`)-এর সমন্বয়ে গঠিত। 

এই পাইপলাইনে কোড পুশ বা পুল রিকোয়েস্ট (Pull Request) হওয়ার সাথে সাথে সমান্তরালে (Parallel) ৫টি কোয়ালিটি গেট এক্সিকিউট হয়—কোড ফরম্যাটিং ও লিন্টিং (Ruff), স্ট্রিক্ট টাইপ চেকিং (Mypy), স্ট্যাটিক সিকিউরিটি অডিট (Bandit ও Semgrep), আর্কিটেকচার বাউন্ডারি গার্ড (AST Module Graph Linter), এবং ফুল রিগ্রেশন টেস্ট স্যুট (Pytest)। সমস্ত আপস্ট্রিম গেট সফলভাবে পাস করলেই কেবল ৬ষ্ঠ ও চূড়ান্ত ধাপে হার্ডেনড মাল্টি-স্টেজ ডকার কন্টেইনার ইমেজ তৈরি ও ভেরিফাই হয়।

---

## ২. 📌 ব্যবহৃত DSA ও ডেভঅপ্স পাইপলাইন প্যাটার্নের নাম
- **সিআই/সিডি অটোমেশন পাইপলাইন ও মাল্টি-স্টেজ কোয়ালিটি গেটওয়ে (Continuous Integration & Continuous Deployment — Multi-Stage DAG Job Pipeline with GitHub Actions)**
- **ডিরেক্টেড অ্যাসাইক্লিক গ্রাফ জব অর্কেস্ট্রেশন (Job Directed Acyclic Graph - DAG Concurrency & Convergence Pattern)**
- **শর্ট-সার্কিট ফেইল-ফাস্ট গেটওয়ে (Short-Circuiting Fail-Fast Barrier Gate)**
- **প্রোডাকশন কন্টেইনার নন-রুট অ্যাটেস্টেশন (Non-Root Container Attestation Pattern - UID 10001)**

---

## ৩. 🚀 প্রোডাকশনে ঠিক কখন ব্যবহার করব? (When to use in Production)
- **প্রতিটি গিট পুশ ও পুল রিকোয়েস্টে:** কোনো ডেভেলপার যাতে লোকাল মেশিনে টেস্ট না চালিয়ে, টাইপিং এরর রেখে বা ক্লিন আর্কিটেকচার বাউন্ডারি ভেঙে সরাসরি `main` ব্রাঞ্চে কোড মার্জ করতে না পারে।
- **অটোমেটেড সিডি (Continuous Deployment) রোলআউটে:** ক্লাউড কুবারনেটিস (K8s) ক্লাস্টারে নতুন রিলিজ দেওয়ার আগে ডকার ইমেজ বিল্ড এবং সিকিউরিটি অডিট সম্পূর্ণ গ্রিন (Green) হওয়া নিশ্চিত করতে।
- **টিম কোলাবোরেশন ও কোড রিভিউতে:** পিআর (Pull Request) ওপেন করার সাথে সাথে বট স্বয়ংক্রিয়ভাবে স্ট্যাটাস জানিয়ে দেয় কোডটি প্রোডাকশন-রেডি কি না, ফলে ম্যানুয়াল রিভিউয়ের সময় সাশ্রয় হয়।
- **মাল্টি-প্ল্যাটফর্ম কম্প্যাটিবিলিটি নিশ্চিতে:** লোকাল মেশিনের ওএস (Windows/macOS) নির্বিশেষে ক্লাউডের একদম ক্লিন উবুন্টু লিনাক্স রানারে ডিপেন্ডেন্সি ও কোড কম্প্যাটিবিলিটি প্রুভ করতে।

---

## ৪. 🎯 কী কারণে বা কোন পরিস্থিতিতে ব্যবহার করব? (Why to use / Technical Triggers)
- **"আমার মেশিনে চলে কিন্তু সার্ভারে চলে না" রোগ চিরতরে দূর করা:** লোকাল ক্যাশ, প্রি-ইন্সটলড লাইব্রেরি বা ড্রাফট কনফিগারেশনের কারণে অনেক সময় কোড ডেভেলপারের ল্যাপটপে চলে কিন্তু প্রোডাকশনে ক্র্যাশ করে। সিআই পাইপলাইনে প্রতিবার নতুন ভার্চুয়াল এনভায়রনমেন্টে কোড ভ্যালিডেট হয়।
- **প্যারালাল জব এক্সেকিউশন (Job DAG):** সব টেস্ট একটার পর একটা চালালে যেখানে ১৫–২০ মিনিট সময় নষ্ট হতো, সেখানে সমান্তরালে (Parallel Fork-Join) লিন্ট, টাইপচেক, সিকিউরিটি ও টেস্ট চালিয়ে সিআই টাইম ১–২ মিনিটে নামিয়ে আনা।
- **প্রোডাকশন কন্টেইনার অ্যাটেস্টেশন:** ভাঙা টেস্ট বা মেলিসিয়াস কোড থাকা অবস্থায় যাতে কোনো ডকার ইমেজ তৈরিই না হয় (`docker-build` জব শুধুমাত্র বাকি ৫টি জব পাস করলেই চলে)।

---

## ৫. বাস্তব জীবনের গল্প ও উপমা (Real-World Analogy)
একটি বিশ্বমানের **স্বয়ংক্রিয় টয়োটা কার ম্যানুফ্যাকচারিং প্ল্যান্ট**-এর কথা কল্পনা করুন। 

গাড়ির বডি তৈরি হওয়ার পর ফাইনাল শো-রুমে পাঠানোর আগে কনভেয়ার বেল্টে ৫টি আলাদা রোবোটিক ইন্সপেকশন স্টেশন সমান্তরালে কাজ করে:
1. **পেইন্ট ও ফিনিশিং স্টেশন (Ruff Lint/Format):** কোনো স্ক্র্যাচ বা দাগ আছে কি না দেখে।
2. **ডাইমেনশন ও স্ট্রাকচারাল স্টেশন (Mypy Strict):** নাট-বল্টুর মাপ মিলিমিটারে নিখুঁত কি না মেপে নেয়।
3. **মেটাল মেটিরিয়াল ও ক্র্যাক স্ক্যানার (Bandit & Semgrep Security):** ভেতরের ধাতু মজবুত কি না এবং কোনো ক্ষতিকর ধাতু আছে কি না এক্স-রে করে।
4. **ইঞ্জিন ও ট্রান্সমিশন আর্কিটেকচার টেস্ট (Architecture Linter):** গিয়ারবক্সের সাথে ইঞ্জিনের কানেকশন সঠিক ব্লুপ্রিন্ট মেনে হয়েছে কি না যাচাই করে।
5. **ক্র্যাশ টেস্ট ও ট্র্যাক টেস্ট (Pytest Suite):** হাই-স্পিডে গাড়ি ড্রাইভ করে ব্রেক ও স্টিয়ারিং ঠিকমতো কাজ করছে কি না দেখে।

যদি এই ৫টি স্টেশনের একটিতেও লাল বাতি জ্বলে, তবে রোবোটিক অ্যাসেম্বলি লাইনের অ্যালার্ম বেজে ওঠে এবং কনভেয়ার বেল্ট থেমে যায়। গাড়ির গায়ে "শিপিং স্ট্যাম্প" (Docker Container Build) পড়ে না। কেবল যখন ৫টি স্টেশনই একযোগে সবুজ বাতি দেয়, তখনই গাড়িটি রিলিজের জন্য সিলগালা করা হয়। আমাদের গিটহাব অ্যাকশনস পাইপলাইন ঠিক এই কারখানার স্বয়ংক্রিয় ইন্সপেকশন লাইন!

---

## ৬. এটা না বানালে কী মহাবিপদ হতো? (Disaster Scenario)
সিআই/সিডি অটোমেশন না থাকলে যা ঘটত:
1. **ভাঙা কোড সরাসরি প্রোডাকশনে:** শুক্রবার বিকেলে একজন ডেভেলপার ক্লান্ত মাথায় টাইপোযুক্ত কোড বা ফেইলিং ডেটাবেস মাইগ্রেশন পুশ করে উইকএন্ডে চলে গেল। সোমবার সকালে দেখা গেল পুরো ইকমার্স সাইট ডাউন (Downtime Disaster)!
2. **লুক্কায়িত সিকিউরিটি ফুটো:** অসাবধানতাবশত কোডে `eval()` বা হার্ডকোডেড মাস্টার এডমিন পাসওয়ার্ড বা র-এসকিউএল ইনজেকশন থেকে গেল। হ্যাকাররা সাইট ডেপ্লয় হওয়ার কয়েক ঘণ্টার মধ্যেই পুরো ডেটাবেস চুরি করে নিল।
3. **আর্কিটেকচারাল কোড স্লপ:** ইঞ্জিনিয়াররা শর্টকাট মারার জন্য কন্ট্রোলারের ভেতরে এসকিউএল কোড বা ওআরএম মডেল কল করা শুরু করল। ৬ মাস পর কোডবেস এমন জগাখিচুড়ি মনোলিথ হলো যে কোনো নতুন ফিচার যোগ করা অসম্ভব হয়ে পড়ল।
4. **পেনিক ডেপ্লয়মেন্ট:** কোন ইমেজটা আসলে প্রোডাকশনে গেছে আর কোন কোডে টেস্ট পাস করেছে তার কোনো ডিজিটাল রেকর্ড বা ট্রাস্টেড অডিট ট্রেইল থাকত না।

---

## ৭. আমাদের প্রজেক্টের আসল কোড ও কোডের লাইন-বাই-লাইন সহজ ব্যাখ্যা

### গিটহাব অ্যাকশনস পাইপলাইন ডক (`.github/workflows/ci.yml`):
```yaml
name: Production CI/CD Pipeline & Quality Gates

'on':
  push:
    branches: [main]
  pull_request:
    branches: [main]

concurrency:
  group: ${{ github.workflow }}-${{ github.ref }}
  cancel-in-progress: true

env:
  PYTHON_VERSION: "3.11"

jobs:
  # স্টেজ ১: কোড কোয়ালিটি ও ফরম্যাটিং
  lint:
    name: "Stage 1: Lint & Code Formatting"
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: ${{ env.PYTHON_VERSION }}
          cache: "pip"
      - run: |
          pip install ruff
          ruff check app tests alembic scripts load_tests
          ruff format --check app tests alembic scripts load_tests

  # স্টেজ ২: স্ট্যাটিক টাইপ চেকিং
  type-check:
    name: "Stage 2: Static Type Checking"
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: ${{ env.PYTHON_VERSION }}
          cache: "pip"
      - run: |
          pip install -r requirements.txt mypy types-redis types-requests types-PyYAML
          mypy --strict app tests alembic scripts

  # স্টেজ ৩: সিকিউরিটি অডিট (SAST)
  security-audit:
    name: "Stage 3: Static Security Audits (SAST)"
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: ${{ env.PYTHON_VERSION }}
          cache: "pip"
      - run: |
          pip install bandit semgrep
          bandit -r app -ll
          semgrep scan --config=.semgrep.yml app

  # স্টেজ ৪: আর্কিটেকচার কমপ্লায়েন্স গেট
  architecture-audit:
    name: "Stage 4: Architecture Compliance Gate"
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: ${{ env.PYTHON_VERSION }}
          cache: "pip"
      - run: python scripts/audit_architecture.py

  # স্টেজ ৫: ফুল রিগ্রেশন টেস্ট স্যুট
  test-suite:
    name: "Stage 5: Test Suite Regression"
    runs-on: ubuntu-latest
    services:
      redis:
        image: redis:7-alpine
        ports: [6379:6379]
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: ${{ env.PYTHON_VERSION }}
          cache: "pip"
      - run: |
          pip install -r requirements.txt pytest pytest-asyncio pytest-cov httpx locust
          pytest --maxfail=1 --durations=10

  # স্টেজ ৬: ডকার ইমেজ বিল্ড ও সিকিউরিটি গেট (কনভার্জেন্স পয়েন্ট)
  docker-build:
    name: "Stage 6: Multi-Stage Container Build Gate"
    runs-on: ubuntu-latest
    needs: [lint, type-check, security-audit, architecture-audit, test-suite]
    steps:
      - uses: actions/checkout@v4
      - uses: docker/setup-buildx-action@v3
      - name: Build Hardened Image
        run: docker build -t fastapi-clean-architecture:latest .
      - name: Verify Non-Root Execution
        run: |
          RUNNER_UID=$(docker run --rm fastapi-clean-architecture:latest id -u)
          if [ "$RUNNER_UID" != "10001" ]; then
            echo "Security Error: Container not running as UID 10001!"
            exit 1
          fi
```

### কোডের লাইন-বাই-লাইন বিশ্লেষণ:
1. `'on': push / pull_request [main]`: শুধুমাত্র মেইন ব্রাঞ্চের পুশ ও পিআরে পাইপলাইন স্বয়ংক্রিয়ভাবে ট্রিগার হয়।
2. `concurrency.cancel-in-progress: true`: একই ব্রাঞ্চে নতুন কমিট পুশ হলে আগের রানিং অকেজো বিল্ড সঙ্গে সঙ্গে ক্যানসেল হয়ে ক্লাউড কম্পিউটিং রিসোর্স ও খরচ বাঁচায়।
3. `jobs.lint, type-check, security-audit, architecture-audit, test-suite`: কোনো `needs` ছাড়া ডিক্লেয়ার করায় গিটহাবের ৫টি আলাদা ক্লাউড ভিএম-এ এই ৫টি কাজ **সম্পূর্ণ সমান্তরালে (In Parallel)** এক্সিকিউট হয়।
4. `needs: [lint, type-check, security-audit, architecture-audit, test-suite]`: ডকার বিল্ড জবটি স্পষ্ট ডিক্লেয়ার করেছে যে আগের ৫টি জবের সবগুলোই যদি `SUCCESS` স্ট্যাটাস দেয়, তবেই ডকার ইমেজ বিল্ড হবে। একটিও ফেল করলে ডকার বিল্ড স্কিপ হয়ে যাবে।
5. `docker run --rm ... id -u`: কন্টেইনার বিল্ডের পর নিশ্চিত করে যে অ্যাপটি রুট ইউজার হিসেবে নয়, বরং নন-রুট `appuser` (UID 10001) হিসেবে চলছে।

---

## ৮. ডিএসএ ও সিআই/সিডি মেকানিক্স (DSA & Concurrency Mechanics)

```
                       [Git Commit / PR Trigger]
                                   |
         +-------------+-----------+-----------+-------------+
         |             |                       |             |
         v             v                       v             v
    [Job: lint]  [Job: type-check]       [Job: security] [Job: arch & tests]
         |             |                       |             |
         +-------------+-----------+-----------+-------------+
                                   |
                    (DAG Join Barrier: All 5 Pass)
                                   |
                                   v
                       [Job: docker-build]
                                   |
                             (Verified)
```

1. **ডিরেক্টেড অ্যাসাইক্লিক গ্রাফ (DAG - Directed Acyclic Graph):**
   সিআই পাইপলাইনের প্রতিটি কাজ এক একটি নোড $V$ এবং নির্ভরতা এক একটি ডিরেক্টেড এজ $E$। এখানে কোনো সাইকেল নেই ($G=(V, E)$ is acyclic)।
2. **ফর্ক-জয়েন কনকারেন্সি (Fork-Join Concurrency):**
   ট্রিগার হওয়ার সময় ১টি ইভেন্ট থেকে ৫টি সমান্তরাল থ্রেড/জব ফর্ক (Fork) হয়, এবং ডকার বিল্ড জবে এসে সবগুলো ফলাফল জয়েন (Join Barrier) করে।
3. **শর্ট-সার্কিট ইনভেরিয়েন্ট (Short-Circuit Evaluation):**
   যদি প্রথম ৩০ সেকেন্ডের মধ্যে `lint` ফেল করে, তবে বিল্ড ফেইলিওর চিহ্নিত হয় এবং দীর্ঘ ডকার বিল্ড চালানোর মতো ভারী রিসোর্স নষ্ট হয় না।

---

## ৯. ইন্টারভিউ ও ভাইভা প্রশ্ন (Interview Preparation)

### প্রশ্ন ১: Continuous Integration (CI) এবং Continuous Delivery/Deployment (CD)-এর পার্থক্য কী?
**উত্তর:**  
- **CI (কন্টিনিউয়াস ইন্টিগ্রেশন):** কোডবেসে নতুন কোড পুশ করার সাথে সাথে স্বয়ংক্রিয়ভাবে কোড ইন্টিগ্রেট করা, লিন্টিং, স্ট্যাটিক টাইপ চেকিং, সিকিউরিটি স্ক্যান এবং টেস্ট রান করে কোডের বিশুদ্ধতা ও কার্যকারিতা যাচাই করা। এর মূল লক্ষ্য হলো "ইন্টিগ্রেশন হেল" দূর করা।
- **CD (কন্টিনিউয়াস ডেলিভারি/ডেপ্লয়মেন্ট):** সিআই গেট সফলভাবে পাস করার পর স্বয়ংক্রিয়ভাবে আর্টফ্যাক্ট (যেমন ডকার ইমেজ) প্যাকেজিং করা এবং স্টেজিং বা প্রোডাকশন কুবারনেটিস ক্লাস্টারে জিরো-ডাউনটাইমে রিলিজ দেওয়া।

### প্রশ্ন ২: Docker Container Build কেন টেস্ট এবং লিন্ট জবের উপর `needs` ডিপেন্ডেন্সিতে থাকা উচিত?
**উত্তর:**  
কন্টেইনার ইমেজ বিল্ড করা একটি অত্যন্ত কম্পিউট-হেভি ও ব্যান্ডউইথ-ইনটেনসিভ প্রসেস (বেস ইমেজ ডাউনলোড, ভেনভ কম্পাইল, ডকার লেয়ার ক্যাশিং ইত্যাদি)। যদি কোডে একটি সাধারণ সিনট্যাক্স এরর, সিকিউরিটি ভালনারেবিলিটি বা ফেইলিং ইউনিট টেস্ট থাকে, তবে সেই ত্রুটিপূর্ণ কোড দিয়ে ইমেজ বানানো সময় ও মেমরির অপচয়। এছাড়া ত্রুটিপূর্ণ ইমেজ ডকার রেজিস্ট্রিতে পুশ হলে ভুলবশত তা প্রোডাকশনে রোলআউট হয়ে ডিজাস্টার ঘটাতে পারে। তাই `needs` দিয়ে স্ট্রিক্ট কোয়ালিটি গেট প্রয়োগ করা আর্কিটেকচারাল বেস্ট প্র্যাকটিস।

---

## ১০. এক নজরে আসল মূল লজিক (Quick Summary)
> **গিট পুশ বা পুল রিকোয়েস্টের সাথে সাথে ৫টি রোবোটিক গেট (Ruff, Mypy, Bandit, Architecture Graph, Pytest) সমান্তরালে ফ্রেশ লিনাক্স রানারে চলে—সবগুলো সবুজ বাতি দিলেই কেবল এন্টারপ্রাইজ নন-রুট ডকার ইমেজ তৈরি হয়, যা প্রোডাকশনে জিরো-ডিফেক্ট রিলিজের গ্যারান্টি দেয়।**
