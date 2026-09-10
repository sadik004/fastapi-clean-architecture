# ডে ৭৭: ডকার কম্পোজ প্রোডাকশন স্ট্যাক অর্কেস্ট্রেশন (FastAPI + PostgreSQL + Redis + Prometheus)

---

### ১. আমরা কী বানিয়েছি? (What did we build?)
আমরা একটি পূর্ণাঙ্গ এন্টারপ্রাইজ মাল্টি-কন্টেইনার অর্কেস্ট্রেশন আর্কিটেকচার তৈরি করেছি যা একটিমাত্র কমান্ডের মাধ্যমে (`docker compose up -d`) আমাদের পুরো ব্যাকএন্ড ইকোসিস্টেমকে ক্লাউড বা লোকাল পরিবেশে সুশৃঙ্খলভাবে চালু করে। এতে রয়েছে:
1. **ফাস্টএপিআই (FastAPI Service)**: ডে ৭৬-এর মাল্টি-স্টেজ ডকারফাইল দ্বারা নির্মিত নন-রুট ব্যাকএন্ড অ্যাপ্লিকেশন।
2. **পোস্টগ্রেসকিউএল ১৫ (PostgreSQL Database)**: প্রাইমারি ট্রানজ্যাকশনাল ডাটাবেস, যা নেমড ভলিউমের মাধ্যমে ডাটা পারসিস্ট করে।
3. **রেডিস ৭ (Redis Cache)**: ডিস্ট্রিবিউটেড ক্যাশ এবং টোকেন ব্লকলিস্ট ইঞ্জিন, AOF (`--appendonly yes`) স্টোরেজসহ।
4. **প্রমিথিউস ২.৪৫ (Prometheus Metrics Scraper)**: ডে ৭৩ ও ৭৪-এর মেট্রিক্স এপিআই থেকে প্রতি ১৫ সেকেন্ড পর পর লাইভ টেলিমেট্রি স্ক্র্যাপার।
5. **হেলথচেক ডিপেনডেন্সি সিকোয়েন্সিং**: কুখ্যাত "স্টার্টআপ রেস কন্ডিশন" চিরতরে দূর করতে `condition: service_healthy` পলিসি।

---

### ২. 📌 ব্যবহৃত DSA ও অর্কেস্ট্রেশন আর্কিটেকচারাল প্যাটার্নের নাম
- **ডকার কম্পোজ অর্কেস্ট্রেশন ও সার্ভিস ডিপেনডেন্সি টপোলজিক্যাল সর্ট (Docker Compose Multi-Container Orchestration — Topological Sort Dependency Resolution with Healthcheck Conditionals)**
- **ডিরেক্টেড অ্যাসাইক্লিক গ্রাফ ও সার্ভিস ডিপেনডেন্সি রেজোলিউশন (DAG Resolution for Container Startup Lifecycle)**
- **নেমড ভলিউম ডাটা ডিউরাবিলিটি প্যাটার্ন (Named Volume Storage Persistence & Isolation)**
- **প্রাইভেট ব্রিজ নেটওয়ার্ক পোর্ট শিল্ডিং (User-Defined Bridge Network Isolation & Zero-Port Leakage)**

---

### ৩. 🚀 প্রোডাকশনে ঠিক কখন ব্যবহার করব? (When to use in Production)
- **নতুন ডেভেলপার অনবোর্ডিং ও লোকাল ডেভ:** নতুন কোনো ইঞ্জিনিয়ার প্রজেক্টে যোগ দিলে তাকে ম্যানুয়ালি পোস্টগ্রেস বা রেডিস ইনস্টল করতে না দিয়ে মাত্র একটি কমান্ডে (`docker compose up`) পুরো স্ট্যাক চালুর ব্যবস্থা করতে।
- **প্রি-প্রোডাকশন স্টেজিং ও সিআই/সিডি অটোমেশন:** গিটহাব অ্যাকশনস বা গিটল্যাব সিআই রানারে আসল ডাটাবেস ও রেডিসের বিরুদ্ধে এন্ড-টু-এন্ড (E2E) ইন্টিগ্রেশন টেস্ট চালাতে।
- **সিঙ্গেল-নোড প্রোডাকশন ভিপিএস (Hetzner, DigitalOcean, AWS EC2):** কুবারনেটিস ক্লাস্টারের জটিলতা ও হাজার ডলার ক্লাউড বিল ছাড়াই ছোট বা মাঝারি প্রোডাকশন সার্ভিস সুরক্ষিতভাবে ডিপ্লয় করতে।

---

### ৪. 🎯 কী কারণে বা কোন পরিস্থিতিতে ব্যবহার করব? (Why to use / Technical Triggers)
- **স্টার্টআপ রেস কন্ডিশন (Startup Race Condition) চিরতরে বন্ধ করা:** সাধারণ `depends_on: [postgres]` শুধু দেখে পোস্টগ্রেস কন্টেইনার তৈরি হয়েছে কি না; কিন্তু পোস্টগ্রেস ডাটাবেস পোর্ট ওপেন করে রেডি হতে ১০-১৫ সেকেন্ড সময় নেয়। এই সময়ে ফাস্টএপিআই ডাটাবেস কানেক্ট করতে গিয়ে `ConnectionRefusedError` দিয়ে ক্র্যাশ করে। `condition: service_healthy` এই বিপর্যয় চিরতরে থামায়।
- **জিরো ডাটা লস (Named Volume Persistence):** কন্টেইনার স্টপ বা আপডেট (`docker compose down`) করলে যাতে ডাটাবেস মুছে না যায়, তার জন্য নেমড ভলিউম (`postgres_data`, `redis_data`) ব্যবহার করা।
- **সিকিউর নেটওয়ার্ক আইসোলেশন:** পোস্টগ্রেস (5432) এবং রেডিস (6379) পোর্ট পাবলিক ইন্টারনেটে এক্সপোজ না করে শুধু অভ্যন্তরীণ ডকার ব্রিজ নেটওয়ার্কে রাখা, যাতে বাইরের কোনো আক্রমণকারী সরাসরি ডাটাবেসে অ্যাটাক চালাতে না পারে।

---

### ৫. বাস্তব জীবনের গল্প ও উপমা (Real-World Analogy)
একটি আন্তর্জাতিক সিম্ফনি অর্কেস্ট্রা কনসার্টের কথা চিন্তা করুন:

মঞ্চে বেহালাবাদক, গিটারিস্ট, ড্রামার এবং মূল গায়ক রয়েছেন। যদি অর্কেস্ট্রা ডিরেক্টর মঞ্চে ওঠার আগেই গায়ক দৌড়ে গিয়ে গান গাওয়া শুরু করে দেন, তখন দেখা যাবে ড্রাম তখনও টিউনিং করা হয়নি, মাইকের লাইন জোড়া দেওয়া হয়নি—পুরো কনসার্ট মুহূর্তেই পণ্ড হয়ে যাবে!

একজন দক্ষ কনসার্ট ডিরেক্টর মঞ্চে উঠে প্রথমে ড্রাম ও গিটারের সুর পরীক্ষা করেন (`healthcheck`), সবাই যখন সুর মিলিয়ে সঙ্কেত দেন (`service_healthy`), ঠিক তখনই তিনি মূল গায়ককে গান শুরু করার ইঙ্গিত দেন। 

আমাদের `docker-compose.yml` হলো সেই কনসার্ট ডিরেক্টর। সে প্রথমে পোস্টগ্রেস এবং রেডিসের সাউন্ড ও কানেকশন টিউন করে নিশ্চিত হয়, তারপরই ফাস্টএপিআই অ্যাপ্লিকেশনকে দর্শকদের সামনে গান গাইতে (ট্রাফিক হ্যান্ডেল করতে) মাঠে নামায়।

---

### ৬. এটা না বানালে কী মহাবিপদ হতো? (The Disaster Scenario)
ধরা যাক, আমরা সাধারণ ডকার কম্পোজ ফাইলে লিখেছি:
```yaml
app:
  depends_on:
    - postgres
```
আমরা প্রোডাকশনে সার্ভার রিবুট দিলাম। ডকার কম্পোজ একই সাথে পোস্টগ্রেস এবং ফাস্টএপিআই কন্টেইনার স্টার্ট করল। পোস্টগ্রেস তার মেমোরি ইনিশিয়ালাইজ করছে, কনফিগ লোড করছে এবং সকেট ওপেন করার চেষ্টা করছে (সময় লাগবে ৫ সেকেন্ড)।

কিন্তু ফাস্টএপিআই ০.১ সেকেন্ডেই বুট হয়ে ডাটাবেসে কানেক্ট করতে গেল। সাথে সাথে ডাটাবেস পোর্ট বন্ধ পেয়ে ফাস্টএপিআই `asyncpg.exceptions.CannotConnectNowError: connection to server at "postgres" failed: Connection refused` ছুড়ে দিল। অ্যাপ্লিকেশন ক্র্যাশ করল। ডকার কন্টেইনারটি `Exited (1)` হয়ে বন্ধ হয়ে গেল। পুরো প্রোডাকশন সার্ভিস ডাউন হয়ে রইল।

---

### ৭. আমাদের প্রজেক্টের আসল কোড ও কোডের লাইন-বাই-লাইন সহজ ব্যাখ্যা

#### (ক) ডকার কম্পোজ কনফিগারেশন (`docker-compose.yml`)
```yaml
version: "3.8"

services:
  postgres:
    image: postgres:15-alpine
    container_name: fastapi_postgres
    restart: unless-stopped
    environment:
      POSTGRES_DB: appdb
      POSTGRES_USER: appuser
      POSTGRES_PASSWORD: apppassword
    volumes:
      - postgres_data:/var/lib/postgresql/data  # কন্টেইনার মুছে গেলেও ডাটা অক্ষত থাকবে
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U appuser -d appdb"] # ডাটাবেস কানেকশন রেডি কি না চেক
      interval: 5s
      timeout: 3s
      retries: 5
    networks:
      - internal_network  # কোনো পোর্ট হোস্ট মেশিনে ওপেন করা হয়নি (সিকিউরিটি ইনভ্যারিয়েন্ট)

  redis:
    image: redis:7-alpine
    container_name: fastapi_redis
    restart: unless-stopped
    command: redis-server --appendonly yes # AOF পারসিস্টেন্স মোড
    volumes:
      - redis_data:/data
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 5s
      timeout: 3s
      retries: 5
    networks:
      - internal_network

  app:
    build:
      context: .
      dockerfile: Dockerfile
    container_name: fastapi_app
    restart: unless-stopped
    ports:
      - "8000:8000" # শুধু এপিআই বাইরের ট্রাফিকের জন্য উন্মুক্ত
    environment:
      DATABASE_URL: postgresql+asyncpg://appuser:apppassword@postgres:5432/appdb
      REDIS_URL: redis://redis:6379/0
      ENV: production
    depends_on:
      postgres:
        condition: service_healthy # পোস্টগ্রেস ১০০% প্রস্তুত না হওয়া পর্যন্ত অপেক্ষা
      redis:
        condition: service_healthy # রেডিস ১০০% প্রস্তুত না হওয়া পর্যন্ত অপেক্ষা
    networks:
      - internal_network

  prometheus:
    image: prom/prometheus:v2.45.0
    container_name: fastapi_prometheus
    restart: unless-stopped
    volumes:
      - ./deployments/docker/prometheus.yml:/etc/prometheus/prometheus.yml:ro
      - prometheus_data:/prometheus
    ports:
      - "9090:9090"
    depends_on:
      - app
    networks:
      - internal_network

volumes:
  postgres_data:
    name: fastapi_postgres_data
  redis_data:
    name: fastapi_redis_data
  prometheus_data:
    name: fastapi_prometheus_data

networks:
  internal_network:
    name: fastapi_internal_network
    driver: bridge
```

#### (খ) প্রমিথিউস স্ক্র্যাপার কনফিগারেশন (`deployments/docker/prometheus.yml`)
```yaml
global:
  scrape_interval: 15s
  evaluation_interval: 15s

scrape_configs:
  - job_name: "fastapi"
    scrape_interval: 15s
    scrape_timeout: 10s
    metrics_path: "/metrics"
    static_configs:
      - targets: ["app:8000"]
        labels:
          service: "fastapi-clean-architecture"
          environment: "production"
```

---

### ৮. ডিএসএ ও কম্পোজ মেকানিক্স (Deep Dive)
- **টপোলজিক্যাল সর্ট ও ডিরেক্টেড অ্যাসাইক্লিক গ্রাফ (DAG):** ডকার কম্পোজ সার্ভিসগুলোর ডিপেনডেন্সি সমাধান করতে গ্রাফ থিওরির **টপোলজিক্যাল সর্টিং (Topological Sort)** অ্যালগরিদম ব্যবহার করে। `app` নির্ভর করে `postgres` ও `redis`-এর ওপর। তাই ডিপেনডেন্সি গ্রাফে `postgres` ও `redis`-এর ইন-ডিগ্রি শূন্য এবং `app`-এর ইন-ডিগ্রি ২। ফলে ইঞ্জিন সবার আগে রুট নোডগুলোকে স্টার্ট করে।
- **Service Healthy Polling Loop ($\mathcal{O}(1)$ Interval Checking):** ডকার ডেমন ব্যাকগ্রাউন্ডে নন-ব্লকিং টাইমার দিয়ে প্রতি ৫ সেকেন্ডে `pg_isready` এক্সিকিউট করে। যতক্ষণ না এক্সিট কোড `0` আসে, ততক্ষণ ডিপেনডেন্ট সার্ভিস `app`-এর কন্টেইনার ক্রিয়েশন ব্লক রাখা হয়।
- **ইউজার-ডিফাইন্ড ব্রিজ নেটওয়ার্ক ও ইন্টারনাল DNS:** ডকার ব্রিজ নেটওয়ার্কে নিজস্ব একটি এমবেডেড DNS সার্ভার (`127.0.0.11`) চালায়। সার্ভিস নেমগুলো (যেমন `postgres`, `redis`, `app`) স্বয়ংক্রিয়ভাবে ঐ নেটওয়ার্কের অভ্যন্তরীণ আইপিতে রিজলভ হয়। ফলে হার্ডকোডেড আইপি অ্যাড্রেসের কোনো প্রয়োজন পড়ে না।

---

### ৯. ইন্টারভিউ ও ভাইভা প্রশ্ন

**প্রশ্ন ১: ডকার কম্পোজে `depends_on` থাকা সত্ত্বেও কেন `condition: service_healthy` ব্যবহার করা বাধ্যতামূলক?**  
**উত্তর:** সাধারণ `depends_on` শুধুই কন্টেইনার প্রসেসটি হোস্ট ওএস লেভেলে শুরু হয়েছে কি না (কন্টেইনার স্ট্যাটাস `running`) তা পরীক্ষা করে। কিন্তু ডাটাবেস বা ক্যাশের মতো স্টেটফুল সার্ভিসগুলো প্রসেস শুরুর পরও পোর্ট ওপেন করতে ও ইন্টারনাল ফাইলসিস্টেম চেক করতে বেশ কয়েক সেকেন্ড সময় নেয়। `condition: service_healthy` ছাড়া ডিপেনডেন্ট এপিআই সাথে সাথে রান করে কানেকশন রিফিউজড এররে ক্র্যাশ করে। হেলথচেক ব্যবহারের মাধ্যমেই কেবল শতভাগ নিশ্চিত হওয়া যায় যে সার্ভিসটি সত্যিই ক্লায়েন্ট রিকোয়েস্ট গ্রহণের জন্য প্রস্তুত।

**প্রশ্ন ২: প্রোডাকশন ডকার কম্পোজে পোস্টগ্রেস বা রেডিসের পোর্ট (`5432:5432`) কেন হোস্ট পোর্টে ম্যাপ করা উচিত নয়?**  
**উত্তর:** প্রোডাকশনে ডাটাবেসের পোর্ট সরাসরি হোস্টে এক্সপোজ করলে ইন্টারনেটের যে কেউ সরাসরি ডাটাবেস পোর্টে ব্রুট-ফোর্স বা ডিডিওএস আক্রমণ চালাতে পারে। ডকার কম্পোজের ইউজার-ডিফাইন্ড ব্রিজ নেটওয়ার্কে সব কন্টেইনার নিজেদের মধ্যে ইন্টারনাল পোর্ট দিয়ে যোগাযোগ করতে পারে। তাই ডাটাবেস পোর্ট আনম্যাপড রেখে শুধু পাবলিক এপিআই গেটওয়ে (যেমন 8000) উন্মুক্ত করাই ন্যূনতম প্রিভিলেজ ও ডিফেন্স-ইন-ডেপথ সিকিউরিটির সেরা প্র্যাকটিস।

---

### ১০. এক নজরে আসল মূল লজিক (২–৩ লাইনে মূল সারমর্ম)
১. `condition: service_healthy` এবং `pg_isready` দিয়ে স্টার্টআপ রেস কন্ডিশন পুরোপুরি দূর করা হয়েছে।  
২. নেমড ভলিউম দিয়ে কন্টেইনার ধ্বংস হলেও ডাটাবেস ও মেট্রিক্সের শতভাগ স্থায়িত্ব বজায় রাখা হয়েছে।  
৩. প্রাইভেট ব্রিজ নেটওয়ার্কে ডাটাবেস পোর্ট সুরক্ষিত রেখে কেবল এপিআই ও প্রমিথিউস পাবলিক রাখা হয়েছে।
