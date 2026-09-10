# Day 70: পোস্টগ্রেস ফুল-টেক্সট সার্চ ও ট্রাইগ্রাম ফাজি সিমিলারিটি আর্কিটেকচার (PostgreSQL Full-Text Search via TSVector, TSQuery & Trigram Similarity)

> **ফেজ ৬ সমাপনী মাইলস্টোন (Sealing Phase 6: Resilience, Fault Tolerance & Database Scaling)**  
> **রোল**: জুনিয়র এপ্রেন্টিস ব্যাকএন্ড ইঞ্জিনিয়ার  
> **মেন্টর ও লিড আর্কিটেক্ট**: ইউজার  

---

## ১. আমরা কী বানিয়েছি? (What did we build?)

আজকে আমরা PostgreSQL-এর সম্পূর্ণ নেটিভ ফিচার ব্যবহার করে একটি এন্টারপ্রাইজ-গ্রেড **ফুল-টেক্সট সার্চ (Full-Text Search) এবং ট্রাইগ্রাম ফাজি সিমিলারিটি (Trigram Fuzzy Similarity)** সার্চ ইঞ্জিন ডিজাইন করেছি। কোনো আলাদা Elasticsearch বা Solr ক্লাউড ক্লাস্টার না বসিয়ে সরাসরি প্রাইমারি ডাটাবেসের ভেতর ইনভার্টেড ইনডেক্স (GIN Index) তৈরি করে সাব-মিলিসেকেন্ড ল্যাটেন্সিতে হাজার হাজার শব্দের ভেতর প্রাসঙ্গিক ডেটা খোঁজা এবং টাইপো বা বানানের ভুল হলেও (যেমন: "aple iphon" দিয়ে "Apple iPhone") নিখুঁতভাবে পণ্য খুঁজে বের করার মেকানিজম প্রতিষ্ঠা করেছি।

আমাদের আর্কিটেকচারে রয়েছে:
1. **ডিক্ল্যারেটিভ সার্চ মডেল (`SearchableProductModel` in `app/models/catalog_search.py`)**: মনোটোনিক UUIDv7 প্রাইমারি কি, Title, Description, Brand, Price এবং একটি বিশেষায়িত `search_vector` (TSVECTOR) কলাম।
2. **ইনভার্টেড GIN ইনডেক্সিং**: TSVector লেক্সিমের উপর GIN ইনডেক্স এবং টাইটেল ও ব্র্যান্ডের উপর ট্রাইগ্রাম GIN ইনডেক্স (`gin_trgm_ops`)।
3. **কোর সার্চ সার্ভিস (`SearchService` in `app/services/search_service.py`)**:
   - **Stemmed Lexeme Full-Text Search**: পোর্টার স্টেনিং রুলস মেনে মূল শব্দ সনাক্ত করে র্যাঙ্ক স্কোরিং (`ts_rank`) সহ কোয়েরি।
   - **Weighted Relevance**: Title-এ শব্দ মিললে Weight A (1.0) এবং Description-এ মিললে Weight B (0.4) দিয়ে প্রায়োরিটি র্যাঙ্কিং।
   - **Trigram Fuzzy Matching**: স্পেলিং মিসটেক হলে ক্যারেক্টার ৩-গ্রাম জ্যাকর্ড সিমিলারিটি দিয়ে সঠিক রেকর্ড ফিল্টার।
   - **হাইব্রিড অটোমেটিক ডিগ্রেডেশন (Hybrid Fallback)**: কোনো সার্চ কি-ওয়ার্ডে এক্স্যাক্ট ফুল-টেক্সট রেজাল্ট ০ আসলে স্বয়ংক্রিয়ভাবে ট্রাইগ্রাম ফাজি সার্চে ডিগ্রেড হয়ে ইউজারের সামনে রেজাল্ট নিয়ে আসা।
   - **অটো-কমপ্লিট সাজেশন ইঞ্জিন**: প্রিফিক্স ও সাবস্ট্রিং ট্রাইগ্রাম ম্যাচিং দিয়ে সাব-মিলিসেকেন্ড সার্চ সাজেশন।
4. **এন্টারপ্রাইজ এন্ডপয়েন্টস (`app/routers/search_router.py`)**: `POST /search/products`, `GET /search/products`, এবং `GET /search/suggest`।
5. **ক্রস-ইঞ্জিন টেস্ট কম্প্যাটিবিলিটি**: SQLite টেস্ট রানারে গাণিতিক ৩-গ্রাম জ্যাকর্ড ক্যালকুলেশন ও পোর্টার স্টেমিং এমুলেশন।

---

## ২. 📌 ব্যবহৃত DSA ও সার্চ অ্যালগরিদমের নাম

- **পোস্টগ্রেস ফুল-টেক্সট সার্চ ও ট্রাইগ্রাম সিমিলারিটি (PostgreSQL Full-Text Search — TSVector Lexeme Inverted Indexing & Trigram Similarity via pg_trgm)**

---

## ৩. 🚀 প্রোডাকশনে ঠিক কখন ব্যবহার করব? (When to use in Production)

1. **ই-কমার্স প্রোডাক্ট সার্চ ও ক্যাটালগ ডিসকভারি**: গ্রাহক যখন ভুল বানান বা খণ্ডিত শব্দ লেখে (যেমন: "nik shoo" দিয়ে "Nike Running Shoes" খোঁজা), তখনও যেন কনভার্সন ড্রপ না করে সঠিক পণ্য স্ক্রিনে তুলে ধরা যায়।
2. **ডকুমেন্ট ও নলেজবেস কনটেন্ট সার্চ**: লক্ষ লক্ষ আর্টিকেলের বড় বডি টেক্সটের ভেতর কোনো প্যারাগ্রাফে নির্দিষ্ট পরিভাষা আলোচনা করা হয়েছে তা সেকেন্ডের ভগ্নাংশে খুঁজে বের করা।
3. **লাইভ অটো-কমপ্লিট ও সার্চ সাজেশন বার**: ইউজার সার্চ বক্সে মাত্র ৩টি অক্ষর টাইপ করলেই (`son`) চোখের পলকে সাজেস্টেড ব্র্যান্ড ও আইটেম পপআপ দেখানো।
4. **জিরো Elasticsearch ক্লাউড খরচ**: ছোট বা মাঝারি স্টার্টআপ টিমে আলাদা সার্চ ক্লাস্টার, রি-ইনডেক্সিং পাইপলাইন এবং ডেটা সিঙ্ক ড্রাফটের ঝামেলা ও হাজার ডলার সার্ভার বিল সম্পূর্ণ বাঁচিয়ে সরাসরি PostgreSQL-এ সার্চ ইঞ্জিন রান করা।

---

## ৪. 🎯 কী কারণে বা কোন পরিস্থিতিতে ব্যবহার করব? (Why to use / Technical Triggers)

1. **`LIKE '%keyword%'` ফুল টেবিল স্ক্যানের ধ্বংসলীলা বন্ধ করতে**: এসকিউএল `LIKE '%text%'` চালালে ডাটাবেস বি-ট্রি ইনডেক্স ব্যবহার করতে পারে না, বাধ্য হয়ে কোটি রো-এর পুরো টেবিল ডিস্ক থেকে মেমরিতে তুলে স্ক্যান করে ($\mathcal{O}(N)$ Full Table Scan)। ফুল-টেক্সট সার্চে ইনভার্টেড ইনডেক্স (GIN) থাকায় প্রতিটি শব্দ সরাসরি পয়েন্ট করা থাকে ($\mathcal{O}(\log N)$)।
2. **শব্দের ব্যাকরণগত রূপভেদ হ্যান্ডেল করা (Porter Stemming & Lemmatization)**: সাধারণ টেক্সট সার্চ "Running" ও "run" কে এক মনে করে না। TSVector দুটিকেই তার মূল ধাতু বা লেক্সিম `run`-এ রূপান্তর করে ফেলে, ফলে ইউজার "run shoe" খুঁজলেও "Running Shoes" পেয়ে যায়।
3. **মাল্টি-ফিল্ড ওয়েটেড র্যাঙ্কিং (Relevance Ranking via `ts_rank`)**: টাইটেলে শব্দ থাকলে তার ওজন বেশি (Weight A), ডেসক্রিপশনে থাকলে ওজন কম (Weight B)। ফলে সবথেকে প্রাসঙ্গিক আইটেমটি সবার উপরে আসে।
4. **টাইপো ও ফাজি মিসটেক টলারেন্স (Trigram Similarity > 0.3)**: মানুষ কীবোর্ডে টাইপ করতে গিয়ে হরহামেশাই ভুল করে। ৩ অক্ষরের স্লাইসিং (3-grams) দিয়ে তৈরি গাণিতিক দূরত্বের মাধ্যমে মিসস্পেলিং নিখুঁতভাবে হ্যান্ডেল করা সম্ভব।

---

## ৫. বাস্তব জীবনের গল্প ও উপমা (Real-World Analogy)

একটি বিশাল ১০ তলা লাইব্রেরির কথা কল্পনা করুন, যেখানে ৫ লাখ বই আছে:

- **LIKE '%keyword%' পদ্ধতি**: একজন লাইব্রেরিয়ানকে আপনি বললেন, "যেসব বইয়ের ভেতর 'কম্পিউটার' শব্দটি আছে আমাকে দিন।" লাইব্রেরিয়ান যদি প্রতিটি আলমারি খুলে প্রতিটা বইয়ের ১ নম্বর পৃষ্ঠা থেকে শেষ পৃষ্ঠা পর্যন্ত চোখ বুলিয়ে খুঁজতে শুরু করে, তবে ১টি বই খুঁজতে তার ৩ সপ্তাহ সময় লেগে যাবে এবং লাইব্রেরির সব কাজ বন্ধ হয়ে যাবে! এটিই হলো `LIKE '%...'` কুয়েরির ফুল টেবিল স্ক্যান।
- **TSVector ও GIN Inverted Index পদ্ধতি**: বইয়ের একেবারে শেষ পাতায় যে "শব্দসূচি বা ইনডেক্স (Back-of-the-book Index)" থাকে, সেখানে ইংরেজি বর্ণানুক্রমে শব্দ লেখা থাকে: "কম্পিউটার ➔ পৃষ্ঠা ১২, ৪৫, ৮৯"। লাইব্রেরিয়ান এক সেকেন্ডে ইনডেক্স পাতা উল্টে নির্দিষ্ট পৃষ্ঠা বের করে ফেলেন। TSVector ঠিক এই কাজটিই করে ডাটাবেসের ভেতর।
- **Trigram Similarity উপমা**: আপনি লাইব্রেরিয়ানকে ভুলে বললেন, "আইপন আছে?" লাইব্রেরিয়ান মনে মনে শব্দটি ভেঙে দেখলেন: `["  আ", " আই", "আইপ", "পন ", "ন  "]`। তিনি তার মেমোরির সাথে মিলিয়ে দেখলেন এটি "আইফোন" শব্দের সাথে ৮০% মিলে যায়। তৎক্ষণাৎ তিনি আপনাকে আইফোনের বইটি এনে দিলেন! এটিই হলো ট্রাইগ্রাম ফাজি সার্চ।

---

## ৬. এটা না বানালে কী মহাবিপদ হতো? (The Production Disaster Without It)

1. **ডাটাবেস সিপিইউ ১০০% হ্যাং ও ক্র্যাশ (The LIKE Catastrophe)**: ব্ল্যাক ফ্রাইডে সেলে যখন হাজার হাজার কাস্টমার সার্চ বক্সে প্রোডাক্ট খুঁজতে শুরু করত, ব্যাকএন্ড থেকে `WHERE title LIKE '%shoes%'` কুয়েরি ডাটাবেসে বোমা পড়ার মতো ফাটত। ১০ লক্ষ রো-এর জন্য ডাটাবেস ১০০% ডিস্ক ও মেমোরি রিড শুরু করত। কানেকশন পুল মুহূর্তেই ড্রাই আউট হয়ে পুরো সাইট ডাউন হয়ে যেত (HTTP 504 Gateway Timeout)।
2. **জিরো কনভার্সন ও কাস্টমার ড্রপআউট (Typo Disaster)**: কাস্টমার মোবাইল স্ক্রিনে হাঁটার সময় দ্রুত টাইপ করতে গিয়ে লিখল "samsng galxy"। সাধারণ কুয়েরি কোনো প্রোডাক্ট খুঁজে পেত না এবং দেখাত "No Products Found!"। কাস্টমার ভাবত ওয়েবসাইটে স্যামসাং ফোন নেই, ফলে সে তৎক্ষণাৎ অন্য ই-কমার্সে চলে যেত। কোম্পানির কোটি টাকার সেলস কনভার্সন নষ্ট হতো।
3. **ওভার-ইঞ্জিনিয়ারিং ও ক্লাউড বিল ব্লাস্ট**: অভিজ্ঞতার অভাবে অনেক টিম ছোট সাইটের জন্যই ৫টি নোডের আলাদা Elasticsearch ক্লাস্টার বানিয়ে মাসে হাজার ডলার খরচ করত এবং ডেটা সিঙ্ক ফেইলিওরে দিনরাত সাফার করত।

---

## ৭. আমাদের প্রজেক্টের আসল কোড ও লাইনের সহজ ব্যাখ্যা (Real Code Breakdown)

### ক. ডিক্ল্যারেটিভ সার্চ মডেল ([`app/models/catalog_search.py`](file:///e:/FastApi1/app/models/catalog_search.py))

```python
class SearchableProductModel(Base):
    __tablename__ = "searchable_products"

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=generate_uuidv7)
    title: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    brand: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    price: Mapped[float] = mapped_column(Float, nullable=False)
    search_vector: Mapped[Any] = mapped_column(TSVECTOR().with_variant(Text, "sqlite"), nullable=True)

    __table_args__ = (
        # ইনভার্টেড GIN ইনডেক্স: TSVector ফিল্ডের দ্রুত সার্চের জন্য
        Index("ix_searchable_products_tsv", "search_vector", postgresql_using="gin"),
        # ট্রাইগ্রাম GIN ইনডেক্স: টাইটেলের ওপর ফাজি সাবস্ট্রিং ও টাইপো সার্চের জন্য
        Index(
            "ix_searchable_products_title_trgm",
            "title",
            postgresql_using="gin",
            postgresql_ops={"title": "gin_trgm_ops"},
        ),
        # ব্র্যান্ডের ওপর ট্রাইগ্রাম ইনডেক্স
        Index(
            "ix_searchable_products_brand_trgm",
            "brand",
            postgresql_using="gin",
            postgresql_ops={"brand": "gin_trgm_ops"},
        ),
    )
```
- **ব্যাখ্যা**: `search_vector` কলামে টাইটেল ও ডেসক্রিপশনের সব শব্দ স্টেম হয়ে সংরক্ষিত থাকে। `postgresql_using="gin"` নির্দেশ করে এটি সাধারণ B-Tree নয়, বরং ইনভার্টেড ইনডেক্স। `gin_trgm_ops` পোস্টগ্রেসকে বলে দেয় এই কলামের ৩-অক্ষরের ট্রাইগ্রাম কেটে ইনডেক্স সাজাও।

### খ. সার্চ সার্ভিস ও হাইব্রিড ডিগ্রেডেশন লজিক ([`app/services/search_service.py`](file:///e:/FastApi1/app/services/search_service.py))

```python
    @classmethod
    async def search_products_hybrid(
        cls,
        session: AsyncSession,
        query_str: str,
        fuzzy: bool = False,
        limit: int = 20,
        similarity_threshold: float = 0.3,
    ) -> SearchResponse:
        clean_query = query_str.strip()
        if not clean_query:
            return SearchResponse(query=query_str, strategy="EMPTY", total=0, results=[])

        # ১. ইউজার জোরপূর্বক ফাজি চাইলে ট্রাইগ্রাম সার্চ
        if fuzzy:
            results = await cls.search_products_fuzzy_trigram(session, clean_query, similarity_threshold, limit)
            return SearchResponse(query=clean_query, strategy="FUZZY_TRIGRAM", total=len(results), results=results)

        # ২. প্রথমে হাই-প্রিসিশন TSVector ফুল-টেক্সট সার্চের চেষ্টা
        ft_results = await cls.search_products_full_text(session, clean_query, limit=limit)
        if ft_results:
            return SearchResponse(query=clean_query, strategy="FULL_TEXT", total=len(ft_results), results=ft_results)

        # ৩. হাইব্রিড অটো ডিগ্রেডেশন: রেজাল্ট ০ আসলে স্বয়ংক্রিয়ভাবে Trigram ফাজি সার্চে সুইচ করা!
        fuzzy_results = await cls.search_products_fuzzy_trigram(session, clean_query, similarity_threshold, limit)
        strategy = "FUZZY_TRIGRAM" if fuzzy_results else "EMPTY"
        return SearchResponse(query=clean_query, strategy=strategy, total=len(fuzzy_results), results=fuzzy_results)
```
- **ব্যাখ্যা**: যদি ইউজার সঠিক ইংরেজি শব্দ দেয়, ফুল-টেক্সট সার্চ সেকেন্ডের ভগ্নাংশে রেজাল্ট এনে দেয়। কিন্তু যদি বানানের ভুলের কারণে ফুল-টেক্সট সার্চ ০ রেজাল্ট দেয়, সিস্টেম থেমে থাকে না; সাথে সাথে ট্রাইগ্রাম ফাজি ইঞ্জিনে পাঠিয়ে টাইপো হ্যান্ডেল করে সঠিক পণ্যটি বের করে আনে!

### গ. ট্রাইগ্রাম সিমিলারিটি ম্যাথমেটিক্স

```python
def _calculate_trigram_similarity(str1: str, str2: str) -> float:
    t1 = _extract_trigrams(str1)
    t2 = _extract_trigrams(str2)
    if not t1 or not t2:
        return 0.0
    intersection = len(t1.intersection(t2))
    union = len(t1.union(t2))
    return round(intersection / union, 4) if union > 0 else 0.0
```
- **ব্যাখ্যা**: দুটি স্ট্রিংয়ের ৩-গ্রাম সেটের ইন্টারসেকশনকে তাদের ইউনিয়ন দিয়ে ভাগ করা হয় (Jaccard Index)। সিমিলারিটি মান `0.0` থেকে `1.0` এর মধ্যে থাকে। `0.3`-এর উপরে মিল থাকলে সেটিকে ম্যাচ হিসেবে গণ্য করা হয়।

---

## ৮. ডিএসএ ও টেক্সট সার্চ মেকানিক্স (DSA & Complexity Analysis)

```
Query Term ──> [Porter Stemmer] ──> Root Lexeme ──> [GIN Inverted Index] ──> Bitmap Scan ──> Matches
                                                         │
                                               Term ➔ [Posting List]
                                              "run" ➔ [Doc1, Doc4, Doc9]
```

1. **ইনভার্টেড ইনডেক্স পোস্টিংস (Inverted Index Postings)**:
   - সাধারণ রিলেশনাল টেবিলে রো আইডি থেকে টেক্সট পাওয়া যায় ($RowID \rightarrow Text$)।
   - ইনভার্টেড ইনডেক্সে টেক্সটের প্রতিটি স্টেম করা শব্দকে কি (Key) বানিয়ে তার বিপরীতে কোন কোন রো আইডিতে শব্দটি আছে তার লিস্ট (Posting List) সেভ রাখা হয় ($Lexeme \rightarrow [ID_1, ID_2, ID_3]$)।
   - কমপ্লেক্সিটি: সাধারণ স্ক্যান যেখানে $\mathcal{O}(N)$, ইনভার্টেড ইনডেক্স টার্ম লুকআপ মাত্র $\mathcal{O}(\log U)$ যেখানে $U$ হলো ইউনিক শব্দের সংখ্যা!
2. **এন-গ্রাম ও ট্রাইগ্রাম শিংলিং (Trigram Shingling)**:
   - স্ট্রিং $S$-এর দৈর্ঘ্য $L$ হলে মোট ট্রাইগ্রাম সংখ্যা $L - 2$।
   - সেট ইউনিয়ন ও ইন্টারসেকশন অপারেশন $\mathcal{O}(|T_1| + |T_2|)$ সময় নেয়, যা স্ট্রিংয়ের আকারের দিক থেকে কার্যত $\mathcal{O}(1)$ কনস্ট্যান্ট টাইম।
3. **পোর্টার স্টেমিং অ্যালগরিদম (Porter Stemming Algorithm)**:
   - ইংরেজি শব্দের প্রত্যয় ও বিভক্তি (Suffixes) ছেঁটে ফেলে মূল ধাতু বের করার ডিসিশন ট্রি। প্রতি শব্দের জন্য $\mathcal{O}(|word|)$ লিনিয়ার টাইমে সম্পন্ন হয়।

---

## ৯. ইন্টারভিউ ও ভাইভা প্রশ্ন (Interview Questions & Model Answers)

### প্রশ্ন ১: `TSVector` এবং `TSQuery` কী? এগুলোতে ইনডেক্সিং কীভাবে কাজ করে?
**মডেল উত্তর**:  
`TSVector` (Text Search Vector) হলো PostgreSQL-এর একটি বিশেষ ডেটা টাইপ যা একটি টেক্সটের সমস্ত অপ্রয়োজনীয় স্টপ-ওয়ার্ড (যেমন: `the`, `is`, `at`) বাদ দিয়ে বাকি শব্দগুলোকে তাদের ব্যাকরণগত মূল রূপ বা লেক্সিমিকে (Lexeme) কনভার্ট করে একটি সর্টেড লিস্ট আকারে রাখে, সাথে শব্দটির অবস্থান ও ওজন (Weight A, B, C, D) জুড়ে দেয়।  
`TSQuery` হলো সার্চ টার্ম যা লজিক্যাল অপারেটর (`&` AND, `|` OR, `!` NOT) দিয়ে তৈরি হয়। যখন `TSVector @@ TSQuery` চালানো হয়, তখন PostgreSQL ম্যাচিং বের করে। এর ওপর **GIN (Generalized Inverted Index)** ব্যবহার করা হয়, যা প্রতিটি লেক্সিমের বিপরীতে রো আইডিগুলোর পোস্টিং লিস্ট তৈরি করে রাখে। ফলে কোটি রো-এর মধ্যেও ফুল-টেক্সট সার্চ সাব-মিলিসেকেন্ডে রেজাল্ট দেয়।

### প্রশ্ন ২: কখন আলাদা Elasticsearch ক্লাস্টার ব্যবহার না করে PostgreSQL-এর ফুল-টেক্সট সার্চ ব্যবহার করাই বুদ্ধিমানের কাজ?
**মডেল উত্তর**:  
যদি অ্যাপ্লিকেশনের মূল ডেটা PostgreSQL-এ থাকে এবং ডেটাসেটের আকার কয়েক কোটি রো পর্যন্ত হয়, তবে আলাদা Elasticsearch না বসিয়ে PostgreSQL FTS ও `pg_trgm` ব্যবহার করাই সবচেয়ে বুদ্ধিমানের কাজ।  
এর ৩টি প্রধান কারণ:  
১. **জিরো সিঙ্ক ল্যাটেন্সি ও ট্রানজ্যাকশন সেফটি**: Elasticsearch-এ ডেটা পাঠাতে ডুয়াল-রাইট বা CDC (Change Data Capture) পাইপলাইন লাগে, যাতে সিঙ্ক ল্যাগ ও ডেটা অসঙ্গতি তৈরি হয়। পোস্টগ্রেসে সার্চ ইনডেক্স সরাসরি ট্রানজ্যাকশনের সাথে এসিড (ACID) কমপ্লায়েন্ট।  
২. **জিরো ক্লাউড খরচ ও অপ্স ওভারহেড**: Elasticsearch ক্লাস্টারের র‍্যাম ও ডিস্ক মেইনটেন্যান্স অনেক ব্যয়বহুল। পোস্টগ্রেস GIN ইনডেক্স অনেক কম মেমরিতে কাজ করে।  
৩. **ফাজি টাইপো টলারেন্স**: `pg_trgm` এক্সটেনশনের মাধ্যমে ট্রাইগ্রাম সিমিলারিটি দিয়ে খুব সহজে ভুল বানান হ্যান্ডেল করা যায়।  
*কেবলমাত্র যখন পেটা-বাইট স্কেলের ডেটা, ডিস্ট্রিবিউটেড লগ অ্যানালিটিক্স বা জটিল মাল্টি-ক্লাস্টার ডিস্ট্রিবিউটেড ল্যাঙ্গুয়েজ সার্চের প্রয়োজন হয়, তখনই Elasticsearch বিবেচনা করা উচিত।*

---

## ১০. এক নজরে আসল মূল লজিক (The Junior Architect's Summary)

> **"প্রোডাকশনে টেক্সট সার্চে কখনো আন-অ্যাঙ্করড `LIKE '%term%'` চালাব না। সবসময় PostgreSQL-এর নেটিভ `TSVector` এবং ইনভার্টেড GIN ইনডেক্স দিয়ে প্রাসঙ্গিকতা র্যাঙ্ক করব (`ts_rank`), আর টাইপো ও বানানের ভুল হ্যান্ডেল করতে `pg_trgm` ট্রাইগ্রাম সিমিলারিটি ব্যবহার করব। হাই-প্রিসিশন সার্চ ফেইল করলে অটোমেটিকভাবে হাই-রিকল ফাজি সার্চে ডিগ্রেড করব।"**
