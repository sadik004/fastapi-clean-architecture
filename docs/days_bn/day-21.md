# Day 21: পাইথন ডিকশনারি ইন্টারনালস — কমপ্যাক্ট হ্যাশ ম্যাপ, পার্টার্বেশন প্রোবিং ও হ্যাশ ডস রেজিলিয়েন্স

---

## ১. আমরা কী বানিয়েছি? (What Did We Build?)

আজ আমরা CPython 3.6+ dictionary-র অভ্যন্তরীণ আর্কিটেকচার হুবহু বাস্তবায়ন করেছি — `app/core/dsa/hash_map.py`-তে একটি পূর্ণাঙ্গ `CompactHashMap` তৈরি করেছি যেটি:
- **Sparse `_indices` array** + **Dense `_entries` array** দিয়ে compact memory layout নিশ্চিত করে
- CPython-এর exact **perturbation probing** recurrence `i = ((5 * i) + 1 + perturb) & mask` বাস্তবায়ন করে
- **Tombstone** (`DUMMY = -2`) দিয়ে deletion-এর পরেও probe chain অক্ষুণ্ন রাখে
- **2/3 load factor**-এ automatic capacity doubling করে
- **Hash DoS attack** সনাক্তকরণ ও telemetry (`diagnose_hash_health`) প্রদান করে

---

## ২. বাস্তব জীবনের গল্প ও উপমা (Real-World Analogy)

কল্পনা করুন একটি বিশাল **টেলিফোন ডিরেক্টরি** বই।

**পুরানো পদ্ধতি (প্রাচীন হ্যাশ টেবিল):**  
প্রতিটি পৃষ্ঠায় নাম-নম্বর-ঠিকানা সরাসরি লেখা। যদি পৃষ্ঠায় ১০০টি ঘর থাকে কিন্তু ৬৫টি ভরা হয়, বাকি ৩৫টি ঘর **ফাঁকা থেকে জায়গা নষ্ট করে**। আর এন্ট্রিগুলো পৃষ্ঠায় এলোমেলো জায়গায় থাকে — নম্বরগুলো ক্রম অনুযায়ী পড়া অসম্ভব।

**আধুনিক পদ্ধতি (CPython Compact Dict — আমাদের বাস্তবায়ন):**
1. **সূচিপত্রের পাতা (Sparse `_indices`):** শুধু ছোট ছোট সংখ্যা লেখা — "এন্ট্রি নম্বর ৩ দেখো", "ফাঁকা (-1)", "মুছে ফেলা হয়েছে (-2)"। এই পাতা অনেক সস্তা — প্রতিটি ঘরে শুধু একটি integer।
2. **মূল রেকর্ড বই (Dense `_entries`):** নাম, নম্বর, ঠিকানা — পরপর ক্রমানুসারে লেখা। যে আগে ঢুকেছে, তার রেকর্ড আগে। **Insertion order গ্যারান্টি!**

**সংঘর্ষ হলে কী হয়?**  
দুই ব্যক্তির নাম একই পৃষ্ঠায় পড়লে (hash collision), ডিরেক্টরি একটি **গোপন সূত্র** দিয়ে পরবর্তী পৃষ্ঠা বের করে: `পরের_পৃষ্ঠা = (৫ × বর্তমান_পৃষ্ঠা + ১ + গোপন_সংখ্যা) mod মোট_পৃষ্ঠা`। এই গোপন সংখ্যা (`perturb`) প্রতি ধাপে ৫ বিট ডানে সরে — ফলে hash-এর উপরের বিটগুলোও কাজে আসে, সংঘর্ষ দ্রুত ছড়িয়ে যায়।

---

## ৩. প্রোডাকশনে কখন এবং কী কারণে এটি ব্যবহার করব? (When & Why in Production)

**প্রোডাকশনে ঠিক কখন ব্যবহার করব?**
- **In-memory cache layer** — Redis/Memcached ছাড়া lightweight local caching (session store, config cache)
- **Deduplication engine** — লক্ষ লক্ষ event-এ duplicate চেক O(1)-এ
- **Custom index** — domain-specific lookup table যেখানে Python dict-এর behaviour বোঝা ও কন্ট্রোল করা দরকার
- **Interview ও system design** — "dict কীভাবে কাজ করে?" — এই প্রশ্নের উত্তর কোড দিয়ে দেখানো

**কী কারণে ব্যবহার করব?**
- CPU L1/L2 **cache locality** — dense entries পরপর মেমোরিতে থাকে, iteration অনেক দ্রুত
- **Insertion-order guarantee** — Python 3.7+ dict-এর গ্যারান্টি বোঝা ও reproduce করা
- **Hash collision resilience** — perturbation probing বোঝা মানে Hash DoS attack বোঝা

---

## ৪. এটা না বানালে কী মহাবিপদ হতো? (The Production Disaster Without It)

**দৃশ্যকল্প: Hash DoS Attack**

একজন আক্রমণকারী জানে আপনার সার্ভারের hash function কীভাবে কাজ করে। সে intentionally এমন ৫০টি key পাঠায় যাদের সবার hash value একই (`42`)।

**নির্বোধ hash table-এ (linear probing):**
```python
# প্রতিটি key একই slot-এ পড়ে → linear scan!
# key 1: slot 42 → ভরা → slot 43 → ভরা → slot 44 → ... → slot 91!
# ৫০তম key-এর lookup: ৫০টি slot check → O(N)!
```

**50 key-এর পরে lookup = O(50) = O(N)।** ১০,০০০ malicious key-এ প্রতিটি lookup O(10,000)। সার্ভার completely freeze হয়ে যায় — **CPU 100%, response timeout, HTTP 503!**

**আমাদের perturbation probing কেন বাঁচায়:**
```python
# i = ((5 * i) + 1 + perturb) & mask; perturb >>= 5
# প্রতি ধাপে perturb 5 বিট ডানে সরে — hash-এর higher bits inject হয়
# ফলে সংঘর্ষকারী key-গুলো ছড়িয়ে যায়, cluster তৈরি হয় না
```

আমাদের test suite-এ **50-key identical hash attack** সফলভাবে সামলানো হয়েছে — কোনো infinite loop বা data corruption নেই।

---

## ৫. আমাদের প্রজেক্টের আসল কোড ও কোডের সহজ ব্যাখ্যা (Real Code & Line-by-Line Breakdown)

### `app/core/dsa/hash_map.py` — Core Architecture

**Sentinel Values ও Data Structure:**
```python
EMPTY: int = -1    # sparse slot-এ কোনো entry নেই
DUMMY: int = -2    # এখানে entry ছিল, মুছে ফেলা হয়েছে (tombstone)
INITIAL_CAPACITY: int = 8  # শুরুতে 8 slot (power of 2)

@dataclass
class Entry(Generic[K, V]):
    hash: int       # key-এর cached hash value (বারবার hash() ডাকতে হবে না)
    key: K          # আসল key
    value: V        # আসল value
    is_active: bool = True  # মুছে ফেলা হলে False
```

**`__init__` — দুই-স্তরের মেমোরি:**
```python
def __init__(self, initial_capacity: int = INITIAL_CAPACITY) -> None:
    capacity = INITIAL_CAPACITY
    while capacity < initial_capacity:
        capacity <<= 1           # ← power-of-2 নিশ্চিত করা
    self._capacity: int = capacity
    self._indices: list[int] = [EMPTY] * self._capacity  # ← sparse array (সূচিপত্র)
    self._entries: list[Entry[K, V]] = []                 # ← dense array (মূল রেকর্ড)
    self._size: int = 0                                    # ← active entry count
```

**`_lookup` — CPython Perturbation Probing:**
```python
def _lookup(self, key: K, hash_val: int) -> tuple[int, int, Optional[int]]:
    mask = self._capacity - 1     # ← capacity power-of-2 হওয়ায় mask = capacity - 1
    i = hash_val & mask           # ← প্রথম slot index (hash mod capacity)
    perturb = hash_val            # ← perturbation seed = পুরো hash value
    first_dummy: Optional[int] = None
    probe_depth = 0

    while True:
        idx = self._indices[i]
        if idx == EMPTY:
            # slot ফাঁকা → key নেই → insertion point return
            target_slot = first_dummy if first_dummy is not None else i
            return (target_slot, EMPTY, first_dummy)

        if idx == DUMMY:
            # tombstone → skip, কিন্তু পরে insert করলে এই slot reuse করা যাবে
            if first_dummy is None:
                first_dummy = i
        else:
            entry = self._entries[idx]
            if entry.is_active and entry.hash == hash_val and entry.key == key:
                return (i, idx, first_dummy)  # ← KEY FOUND!

        # collision! → CPython recurrence formula:
        self._collision_count += 1
        probe_depth += 1
        i = ((5 * i) + 1 + perturb) & mask   # ← THE MAGIC FORMULA
        perturb >>= 5                          # ← 5-bit right shift
```

**কেন `5i + 1` full period traversal নিশ্চিত করে?**  
Capacity সবসময় $2^k$। $f(i) = 5i + 1 \pmod{2^k}$ একটি **full-period permutation** — $2^k$ টি distinct slot visit করে repeat করার আগে। গাণিতিক কারণ: $\gcd(5, 2^k) = 1$ (5 বিজোড়, $2^k$ জোড়), তাই $5i + 1$ multiplicative group-এ একটি generator। Perturbation ($\text{perturb} \gg 5$) এর উপরে hash-এর higher bits inject করে collision path আরো scatter করে।

---

### `__setitem__` — Insert with 2/3 Load Factor

```python
def __setitem__(self, key: K, value: V) -> None:
    hash_val = hash(key)
    slot, entry_idx, first_dummy = self._lookup(key, hash_val)

    if entry_idx != EMPTY:
        self._entries[entry_idx].value = value  # ← key exists, update in-place
        return

    # 2/3 load factor check: (entries + 1) * 3 >= capacity * 2
    if (len(self._entries) + 1) * 3 >= self._capacity * 2:
        self._resize(self._capacity * 2)         # ← capacity double!
        slot, _, _ = self._lookup(key, hash_val)  # ← re-probe after resize

    new_entry_idx = len(self._entries)
    self._entries.append(Entry(hash=hash_val, key=key, value=value, is_active=True))
    self._indices[slot] = new_entry_idx
    self._size += 1
```

**কেন 2/3 এবং 3/4 নয়?**  
Load factor বেশি হলে collision বাড়ে → lookup ধীর হয়। CPython 2/3 (~66.7%) বেছেছে কারণ এটি collision probability কে যথেষ্ট কম রাখে যেন average probe depth < 2 থাকে, কিন্তু memory waste-ও 50% (load factor 0.5)-এর চেয়ে কম।

---

### `delete` — Tombstone Preservation

```python
def delete(self, key: K) -> bool:
    hash_val = hash(key)
    slot, entry_idx, _ = self._lookup(key, hash_val)
    if entry_idx == EMPTY:
        return False

    self._entries[entry_idx].is_active = False   # ← dense entry inactive
    self._indices[slot] = DUMMY                   # ← sparse slot → tombstone
    self._size -= 1
    return True
```

**Tombstone কেন দরকার?**  
ধরুন slot 5-এ key A ঢুকেছে, collision-এ slot 12-এ key B ঢুকেছে। এখন A মুছলে slot 5 EMPTY করলে, B খুঁজতে গেলে slot 5-এ EMPTY দেখবে → "key নেই" বলবে → **B হারিয়ে যাবে!** DUMMY মানে "এখানে কিছু ছিল, এগিয়ে যাও" — probe chain অক্ষুণ্ন থাকে।

---

## ৬. ডিএসএ ও অ্যালগরিদমিক মেকানিক্স (DSA Complexity Made Simple)

| অপারেশন | Time Complexity | ব্যাখ্যা |
|---|---|---|
| `__getitem__` (lookup) | **O(1)** amortized | hash → slot → probe (avg < 2 probes at 2/3 load) |
| `__setitem__` (insert) | **O(1)** amortized | probe + append to dense array |
| `delete` | **O(1)** amortized | probe + tombstone mark |
| `_resize` | **O(N)** worst-case | re-index all N active entries; amortized O(1) over N inserts |
| `keys/values/items` | **O(N)** | dense array sequential scan — cache-friendly |
| `diagnose_hash_health` | **O(1)** | pre-computed collision/depth counters |

**Memory Layout:**
- Sparse array: $2^k$ integers (প্রতিটি ~8 bytes) → $8 \times 2^k$ bytes
- Dense array: $N$ entries (hash + key + value + bool) → contiguous in memory
- **Total: O(N)** — পুরানো hash table-এর তুলনায় ~25% memory সাশ্রয়

---

## ৭. এই দিনের RCA ও বাস্তব ভুল থেকে শিক্ষা (RCA Incident)

**Incident: 50-Key Hash Collision Attack Simulation**

Test suite-এ ৫০টি ভিন্ন key জোর করে একই hash value `42` দিয়ে inject করা হয়েছে:

```python
class ForcedCollisionKey:
    def __init__(self, name: str):
        self.name = name
    def __hash__(self) -> int:
        return 42  # ← সব key-র hash একই!
    def __eq__(self, other):
        return self.name == other.name
```

**ফলাফল:** Perturbation probing সবগুলো key সফলভাবে store ও retrieve করেছে — কোনো infinite loop, data loss বা corruption নেই। `diagnose_hash_health` সঠিকভাবে `"pathological_collision_attack_detected"` রিপোর্ট করেছে।

**স্থায়ী শিক্ষা:**
1. Linear probing (`i + 1`) primary clustering তৈরি করে — $O(N)$ degrade হয়
2. Perturbation probing higher hash bits inject করে clustering ভাঙে
3. Production-এ hash collision telemetry monitor করলে DoS attack আগেই ধরা যায়

---

## ৮. ইন্টারভিউ ও ভাইভা প্রশ্ন (Interview Questions & Model Answers)

**প্রশ্ন ১:** "Python dict-এ insertion order কীভাবে guarantee হয়?"

**মডেল উত্তর:** CPython 3.6+ দুই-স্তরের compact layout ব্যবহার করে। Sparse array-তে শুধু integer indices থাকে, dense array-তে `(hash, key, value)` entries ক্রমানুসারে append হয়। Iteration-এ dense array sequential scan করলেই insertion order পাওয়া যায়। পুরানো hash table-এ entries sparse array-তেই ছড়িয়ে থাকতো — order গ্যারান্টি ছিল না।

---

**প্রশ্ন ২:** "Hash collision resolve করতে CPython কোন algorithm ব্যবহার করে?"

**মডেল উত্তর:** Open addressing with perturbation probing। Recurrence: `i = (5*i + 1 + perturb) & mask; perturb >>= 5`। Capacity সবসময় $2^k$, তাই `5i+1 mod 2^k` full-period permutation — সব slot exactly একবার visit করে। `perturb >>= 5` higher hash bits inject করে — identical lower bits-এর collision cluster ভাঙে। Chaining (linked list) ব্যবহার করে না কারণ linked list cache-unfriendly।

---

## ৯. এক নজরে আসল মূল লজিক (The Core Bottom-Line Logic)

> **সারমর্ম:** Python dictionary হলো দুটি array-র মিলন — একটি ছোট সূচিপত্র (sparse), একটি ঘন রেকর্ড বই (dense)। Key খুঁজতে hash-এ probe করো, collision হলে `5i + 1 + perturb` জাম্প করো, মুছলে tombstone রাখো যাতে পরের key হারিয়ে না যায়। 2/3 ভরলেই দ্বিগুণ করো। **O(1) lookup, O(N) compact memory, insertion-order guarantee — এটাই CPython dict-এর হৃদয়।**
