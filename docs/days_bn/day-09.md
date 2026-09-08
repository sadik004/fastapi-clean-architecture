# ডে ০৯: সাব-ডিপেনডেন্সি চেইনিং, প্যারামিটারাইজড ক্লাস গার্ড এবং আইডি ওআর (IDOR) ডিফেন্স

> **তারিখ**: ২০২৬-০৯-০৮  
> **ভূমিকা**: জুনিয়র শিক্ষানবিস ব্যাকএন্ড ইঞ্জিনিয়ার  
> **মেন্টর ও লিড আর্কিটেক্ট**: ইউজার  
> **মূল দর্শন**: রাউটার হ্যান্ডলারে কোনো ম্যানুয়াল সিকিউরিটি চেক নয়—FastAPI-এর ডিপেনডেন্সি ইনভার্সন এবং ডিরেক্টেড অ্যাসাইক্লিক গ্রাফ (DAG)-এর শক্তিতে গড়ে তুলুন নিশ্ছিদ্র অ্যাক্সেস কন্ট্রোল বাউন্ডারি।

---

## ১. বাস্তব জীবনের রূপক: ফাইভ-স্টার হোটেলের স্মার্ট কি-কার্ড বনাম মাস্টার কি

কল্পনা করুন আপনি একটি আধুনিক ফাইভ-স্টার বিজনেস হোটেলে উঠেছেন:

1. **রিসিপশনের সিকিউরিটি গেট (`get_current_user`)**:
   - হোটেলে প্রবেশ করার সময় রিসিপশনে আপনার পাসপোর্ট/আইডি কার্ড প্রদর্শন করতে হয়। রিসিপশনিস্ট যাচাই করে আপনাকে একটি আরএফআইডি (RFID) স্মার্ট রুম কি-কার্ড প্রদান করে। এই কি-কার্ডে আপনার পরিচয় এবং আপনার নির্ধারিত রুম নম্বর (যেমন: Room 402) লেখা থাকে।
2. **ব্যক্তিগত রুমের মালিকানা লক (`require_user_ownership`)**:
   - আপনি যখন ৪র্থ তলার ৪০২ নম্বর রুমের দরজায় কার্ড ছোঁয়ান, দরজার স্মার্ট লক চেক করে: *"এই কার্ডের মালিক কি রুম ৪০২-এর বোর্ডার?"* যদি হ্যাঁ হয়, দরজা খুলে যায়।
   - কিন্তু আপনি যদি ভুল করে বা ইচ্ছাকৃতভাবে পাশের ৪০৩ নম্বর রুমে আপনার কার্ড ছোঁয়ান, লক লাল বাতি জ্বালিয়ে অ্যাক্সেস রিজেক্ট করবে (`403 Forbidden`)। কারণ আপনি বৈধ অতিথি হলেও ৪০৩ নম্বর রুমের মালিক নন!
3. **হোটেল ম্যানেজারের মাস্টার কি (`RoleChecker([UserRole.ADMIN])`)**:
   - হোটেলের জেনারেল ম্যানেজারের কাছে একটি বিশেষ গোল্ডেন মাস্টার কার্ড থাকে। এই কার্ড দিয়ে ম্যানেজার হোটেলের যেকোনো রুম (৪০১, ৪০২, ৪০৩) খুলতে পারেন, এমনকি ৫ম তলার এক্সিকিউটিভ ভল্ট ও সার্ভার রুমেও (`GET /users/admin/metrics`) প্রবেশ করতে পারেন। কিন্তু সাধারণ গেস্ট সেই সার্ভার রুমের দরজায় গেলে সাথে সাথে সাইরেন বেজে উঠবে।
4. **একবার ভেরিফিকেশন, বারবার ব্যবহার (FastAPI Request DAG Caching)**:
   - আপনি যখন রুমে ঢোকেন, রুমের এয়ার কন্ডিশনার, মিনিবার এবং রুম সার্ভিস অর্ডার স্ক্রিন—সবাই আপনার কি-কার্ড থেকেই তথ্য পড়ে। কিন্তু প্রতিবার আপনাকে রিসিপশনে গিয়ে পাসপোর্ট দেখাতে হয় না; হোটেলের ইন্টারনাল নেটওয়ার্ক একবার কার্ড সোয়াইপ করলেই পুরো সেশনের জন্য তথ্য মেমোইজ করে নেয়।

এই রূপকটিই হলো আমাদের ডে ০৯-এর আর্কিটেকচার: **`RoleChecker`** হলো রোবট সিকিউরিটি গার্ড, **`require_user_ownership`** হলো দরজার ব্যক্তিগত মালিকানা যাচাইকারী, আর **`FastAPI DAG Cache`** হলো রিকোয়েস্টের ভেতর একবার ভেরিফাই করে বারবার সেই অথেন্টিকেশন ডেটা ব্যবহার করার ইন্টেলিজেন্ট ইঞ্জিন।

---

## ২. প্রোডাকশন সিস্টেমের বিপর্যয় ও পেছনের গল্প (The "Why" Behind The Architecture)

### বিপর্যয় ১: আইডি ওআর (Insecure Direct Object Reference) এর মাধ্যমে ডেটা চুরি ও প্রোফাইল হাইজ্যাকিং
বহু প্রজেক্টে ডেভেলপাররা খুব সরল মনে একটি প্যাচ রাউট তৈরি করেন:
```python
# মারাত্মক অনিরাপদ কোড
@router.patch("/{user_id}")
def update_profile(user_id: int, payload: UserUpdate, service: UserService):
    return service.update(user_id, payload)
```
এখানে একজন হ্যাকার `User 101` হিসেবে লগইন করে। এরপর সে তার বার্প স্যুট (Burp Suite) বা কার্ল (cURL) কমান্ডে `PATCH /users/102` পাঠিয়ে টার্গেট ইউজারের ইমেইল পরিবর্তন করে নিজের ইমেইল বসিয়ে দেয় এবং "Forgot Password" ট্রিগার করে পুরো অ্যাকাউন্ট হাইজ্যাক করে নেয়! একে বলা হয় **IDOR (OWASP Top 10 API Security Flaw)**। 

যদি কোনো রাউটার হ্যান্ডলার শুধুমাত্র ইউআরএল পাথের `user_id` গ্রহণ করে কোনো ওনারশিপ বা অ্যাডমিন প্রিভিলেজ যাচাই না করেই ডেটাবেজে মিউটেশন চালিয়ে দেয়, তবে সেই পুরো সিস্টেম উন্মুক্ত হয়ে পড়ে।

### বিপর্যয় ২: হ্যান্ডলার ফাংশনে বয়লারপ্লেট `if/else` জঙ্গল ও পারমিশন লিক
অনেক ব্যাকএন্ডে ডেভেলপাররা প্রতিটি ফাংশনের ভেতর এভাবে কোড লেখেন:
```python
@router.put("/{user_id}")
def update_user(user_id: int, user: User = Depends(get_user)):
    if user.id != user_id and user.role != "admin":
        raise HTTPException(403)
    # বিজনেস লজিক...
```
এইভাবে ৩০টি রাউটে ৩০ বার কপি-পেস্ট করা হয়। একদিন কোনো জুনিয়র ডেভেলপার একটি নতুন `PATCH /users/{user_id}/billing` রাউটে এই `if` কন্ডিশন লিখতে ভুলে যান—আর সাথে সাথে প্রোডাকশনে ভয়াবহ সিকিউরিটি ডেটা ব্রিচ ঘটে!

**সমাধান**: ডিক্লেয়ারেটিভ ডিপেনডেন্সি ইনভার্সন। রাউটার কোনো ম্যানউয়াল চেক করবে না। রাউটের সিগনেচারেই গার্ড ডিক্লেয়ার করা থাকবে:
```python
current_user: Annotated[UserEntity, Depends(require_user_ownership)]
```
গার্ড সন্তুষ্ট না হলে কন্ট্রোল রাউটার ফাংশনের দরজাতেই পৌঁছাবে না।

---

## ৩. আর্কিটেকচারাল ডিজাইন এবং ডিপেনডেন্সি ডিরেক্টেড অ্যাসাইক্লিক গ্রাফ (FastAPI Dependency DAG)

FastAPI-এর ডিপেনডেন্সি ইনজেকশন সিস্টেম কেবল সরল প্যারামিটার পাসিং নয়; এটি একটি সম্পূর্ণ **Directed Acyclic Graph (DAG)** এক্সিকিউশন ইঞ্জিন।

```
                  ┌──────────────────────┐
                  │   HTTP Request       │
                  └──────────┬───────────┘
                             │
            ┌────────────────┴────────────────┐
            ▼                                 ▼
┌───────────────────────┐         ┌───────────────────────┐
│ Path Param (user_id)  │         │  get_current_user     │
└───────────┬───────────┘         └───────────┬───────────┘
            │                                 │ (Memoized via
            │                                 │  use_cache=True)
            │      ┌──────────────────────────┴──────────┐
            ▼      ▼                                     ▼
┌──────────────────────────────┐              ┌──────────────────────┐
│    require_user_ownership    │              │     RoleChecker      │
└──────────────┬───────────────┘              └──────────┬───────────┘
               │                                         │
               ▼                                         ▼
   [200 OK or 403 Forbidden]                 [200 OK or 403 Forbidden]
               │                                         │
               └─────────────────┬───────────────────────┘
                                 ▼
                     ┌───────────────────────┐
                     │ Endpoint Handler Code │
                     └───────────────────────┘
```

### প্যারামিটারাইজড কলেজেবল ক্লাস গার্ড (`RoleChecker`)
পাইথনের যেকোনো ক্লাসে যদি `__call__` মেথড ডিফাইন করা থাকে, তবে সেই ক্লাসের অবজেক্টকে ফাংশনের মতো কল করা যায় (`callable`)। FastAPI-এর `Depends()` যেকোনো ক্যালবলকে ডিপেনডেন্সি হিসেবে গ্রহণ করতে পারে!
- আমরা `RoleChecker`-এর কনস্ট্রাক্টরে অনুমোদিত রোলগুলো গ্রহণ করি এবং সেগুলোকে সাথে সাথে একটি ইমিউটেবল `frozenset[str]`-এ রূপান্তরিত করি।
- এর ফলে মেম্বারশিপ চেক হয় কঠোরভাবে $\mathcal{O}(1)$ কনস্ট্যান্ট টাইমে।
- এটি আমাদের একগুচ্ছ সিঙ্গেল-ফাংশন তৈরি করা (`is_admin`, `is_moderator`, `is_admin_or_moderator`) থেকে মুক্তি দেয়। একটি মাত্র ক্লাস দিয়ে যেকোনো রোলের সমন্বয় তৈরি করা যায়:
  ```python
  RoleChecker([UserRole.ADMIN, UserRole.ENTERPRISE])
  ```

### সাব-ডিপেনডেন্সি মেমোইজেশন (`use_cache=True`)
যদি একটি রাউটে `require_user_ownership` এবং একই সাথে কোনো লগার বা আরেকটি ডিপেনডেন্সি উভয়ই `get_current_user`-এর ওপর নির্ভর করে, তবে কি ডেটাবেজে দুবার কুয়েরি হবে?
**না!** FastAPI ডিফল্টভাবে প্রতিটি ডিপেনডেন্সিকে `use_cache=True` হিসেবে রান করে। একটি নির্দিষ্ট HTTP রিকোয়েস্টের লাইফসাইকেলে একই ডিপেনডেন্সি একাধিক নোডে সাব-ডিপেনডেন্সি হিসেবে চেইন করা থাকলে FastAPI গ্রাফের রুট নোডটি **কঠোরভাবে মাত্র একবার** এক্সিকিউট করে এবং তার রিটার্ন ভ্যালু মেমোরি ক্যাশ থেকে অন্য নোডগুলোতে ডিস্ট্রিবিউট করে।

---

## ৪. কোডবেসের বাস্তব কোড ব্যবচ্ছেদ (Line-by-Line Grounded Code Walkthrough)

আসুন আমাদের প্রোডাকশন কোডবেস `app/core/dependencies.py` এবং `app/routers/user_router.py` থেকে সরাসরি লাইন ধরে ব্যবচ্ছেদ করি:

### ক. `RoleChecker` ক্লাস গার্ড (`app/core/dependencies.py`)

```python
class RoleChecker:
    """Role-Based Access Control (RBAC)-এর জন্য প্যারামিটারাইজড কলেজেবল ক্লাস।
    frozenset ব্যবহার করে O(1) মেম্বারশিপ চেকিং নিশ্চিত করে।
    """

    def __init__(
        self,
        allowed_roles: Sequence[UserRole | str] | set[UserRole | str],
        detail: str = "Insufficient role permissions",
    ) -> None:
        # সমস্ত রোলকে নরম্যালাইজ করে frozenset-এ রাখা হচ্ছে O(1) লুকআপের জন্য
        self.allowed_roles: frozenset[str] = frozenset(
            r.value if isinstance(r, UserRole) else str(r) for r in allowed_roles
        )
        self.detail: str = detail

    async def __call__(
        self,
        current_user: Annotated[UserEntity, Depends(get_current_user)],
    ) -> UserEntity:
        # বর্তমান ইউজারের রোল স্ট্রিং এক্সট্র্যাক্ট করা
        user_role_str = (
            current_user.role.value
            if isinstance(current_user.role, UserRole)
            else str(current_user.role)
        )
        # O(1) মেম্বারশিপ ইনস্পেকশন
        if user_role_str not in self.allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=self.detail,
            )
        return current_user
```
- **লাইন ১২৫-১৩৩**: `__init__` এ রোলগুলোকে ইমিউটেবল `frozenset`-এ সিল করা হয়েছে। এটি মেমরি সেফ এবং থ্রেড-সেফ।
- **লাইন ১৩৫-১৩৮**: `__call__` মেথডটি নিজে `Depends(get_current_user)` ডিপেনডেন্সিকে ইনজেক্ট করছে! এটাই হলো **সাব-ডিপেনডেন্সি চেইনিং**।
- **লাইন ১৪৪-১৪৮**: যদি রোল না মিলে, সাথে সাথে `403 Forbidden` রিটার্ন হয়।

### খ. আইডি ওআর প্রোটেকশন গার্ড (`require_user_ownership`)

```python
async def require_user_ownership(
    user_id: Annotated[
        int,
        Path(
            ...,
            ge=1,
            le=2_147_483_647,
            description="The unique positive integer ID of the user",
        ),
    ],
    current_user: Annotated[UserEntity, Depends(get_current_user)],
) -> UserEntity:
    """IDOR সুরক্ষা নিশ্চিতকারী হায়ারার্কিকাল অথরাইজেশন গার্ড।
    শুধুমাত্র রিসোর্সের মালিক অথবা অ্যাডমিনকে অনুমতি প্রদান করে।
    """
    user_role_str = (
        current_user.role.value
        if isinstance(current_user.role, UserRole)
        else str(current_user.role)
    )
    is_owner = current_user.id == user_id
    is_admin = user_role_str == UserRole.ADMIN.value

    if not (is_owner or is_admin):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access forbidden: you cannot modify another user's profile",
        )
    return current_user
```
- **লাইন ১৬০-১৬৮**: ইউআরএল পাথ থেকে `user_id` ভ্যালিডেট করে ইন্টিজার হিসেবে আনা হচ্ছে।
- **লাইন ১৬৯**: সাব-ডিপেনডেন্সি হিসেবে অথেন্টিকেটেড `current_user` ইনজেক্ট হচ্ছে।
- **লাইন ১৮১-১৮৮**: ডুয়াল-লজিক ভ্যালিডেশন: `is_owner or is_admin`। যদি আপনি মালিক হন তবে আপডেট করতে পারবেন, অথবা আপনি যদি অ্যাডমিন হন তবে ওভাররাইড করতে পারবেন। অন্যথায় কঠোর `403 Forbidden`।

### গ. রাউটার লেয়ারে গার্ড প্রয়োগ (`app/routers/user_router.py`)

```python
@router.put(
    "/{user_id}",
    response_model=UserResponse,
    summary="Update an existing user profile (Full replacement)",
)
async def update_user(
    payload: UserUpdate,
    user_id: Annotated[int, Path(..., ge=1, le=2_147_483_647)],
    service: Annotated[UserService, Depends(get_user_service)],
    current_user: Annotated[UserEntity, Depends(require_user_ownership)],
    tx: Annotated[ScopedTransactionContext, Depends(get_transaction_context)],
) -> UserResponse:
    # এখানে কোনো "if current_user.id != user_id" চেক নেই!
    # কন্ট্রোল এখানে আসার আগেই require_user_ownership ওনারশিপ গ্যারান্টি দিয়েছে।
    tx.stage(f"PUT /users/{user_id}")
    updated_user = service.update_user(user_id=user_id, payload=payload)
    return UserResponse.model_validate(updated_user)
```

---

## ৫. কমপ্লেক্সিটি ও অ্যালগরিদমিক অপটিমাইজেশন (DSA Guarantees)

| অপারেশন | ডেটা স্ট্রাকচার / মেকানিজম | টাইম কমপ্লেক্সিটি | স্পেস কমপ্লেক্সিটি | টেকনিক্যাল কারণ |
| :--- | :--- | :--- | :--- | :--- |
| **রোল ভ্যালিডেশন** | `frozenset[str]` | $\mathcal{O}(1)$ | $\mathcal{O}(k)$ ($k$ = রোলের সংখ্যা) | পাইথনের হ্যাশ সেট মেকানিজমে কি সরাসরি হ্যাশ টেবিল লুকআপ করে। কোনো লিনিয়ার লিস্ট স্ক্যান নেই। |
| **ওনারশিপ চেক** | Primitive Integer Equality | $\mathcal{O}(1)$ | $\mathcal{O}(1)$ | মেমোরিতে দুটি ইন্টিজারের ডিরেক্ট তুলনা (`current_user.id == user_id`)। |
| **সাব-ডিপেনডেন্সি রেজোলিউশন** | FastAPI Request Cache (`dict`) | $\mathcal{O}(1)$ | $\mathcal{O}(D)$ ($D$ = গ্রাফের নোড সংখ্যা) | প্রথমবার এক্সিকিউশনের পর ডিপেনডেন্সি পয়েন্টার কি হিসেবে ইন্টারনাল ডিকশনারিতে সেভ থাকে। |

---

## ৬. বাস্তব বাগ ও আরসিএ ব্যবচ্ছেদ (Real RCA: Root Cause Analysis)

আমাদের প্রজেক্টের আসল আরসিএ ফাইল `docs/rca/day-09_dependency_override_recursion_and_idor_protection.md` থেকে দুটি গুরুত্বপূর্ণ ইনসিডেন্ট পর্যালোচনা করা যাক:

### ইনসিডেন্ট ১: ডিপেনডেন্সি ওভাররাইডের সেলফ-রেফারেনশিয়াল ইনফিনিট রিকার্সন ক্র্যাশ

#### ক্র্যাশ সৃষ্টিকারী টেস্ট কোড:
```python
# tests/test_dependency_chaining.py
def spy_get_current_user(
    real_user: UserEntity = Depends(get_current_user),
) -> UserEntity:
    call_count += 1
    return real_user

# মারাত্মক ভুল: ডিপেনডেন্সি নিজেকেই ওভাররাইড করছে!
app.dependency_overrides[get_current_user] = spy_get_current_user
```

#### রুট কজ (Root Cause):
FastAPI যখন কোনো রিকোয়েস্টের জন্য ডিপেনডেন্সি সলভ করতে যায়, সে দেখে `get_current_user`-এর একটি ওভাররাইড আছে `spy_get_current_user`। এরপর FastAPI `spy_get_current_user`-এর সিগনেচার পরীক্ষা করতে গিয়ে দেখে সে আবার `Depends(get_current_user)` চায়! তখন ইঞ্জিন আবার ওভাররাইড ডিকশনারিতে গিয়ে আবার `spy_get_current_user` খুঁজে পায়। এর ফলে একটি অন্তহীন সাইক্লিক লুপ তৈরি হয়:
```text
solve_dependencies(spy_get_current_user)
  -> solve_dependencies(get_current_user) -> override: spy_get_current_user
    -> solve_dependencies(spy_get_current_user)
      ...
RecursionError: maximum recursion depth exceeded
```

#### আর্কিটেকচারাল সমাধান:
কখনোই `app.dependency_overrides`-এ যে ফাংশনকে ওভাররাইড করা হচ্ছে, তাকেই আবার সাব-ডিপেনডেন্সি হিসেবে কল করবেন না। এক্সিকিউশন কাউন্ট বা স্পাই করতে হলে আন্ডারলাইং সার্ভিস মেথডকে `monkeypatch` করুন:
```python
original_get_by_username = UserService.get_user_by_username

def spy_get_by_username(svc_self: UserService, username: str) -> UserEntity:
    nonlocal call_count
    call_count += 1
    return original_get_by_username(svc_self, username=username)

monkeypatch.setattr(UserService, "get_user_by_username", spy_get_by_username)
```

---

## ৭. টেস্টিং স্ট্র্যাটেজি ও কোয়ালিটি গেট (Testing Strategy & Edge Case Verification)

ডে ০৯-এর নিরাপত্তা যাচাই করতে `tests/test_dependency_chaining.py`-তে ৩টি মূল সুরক্ষাবলয় পরীক্ষা করা হয়:

1. **রোল বেসড অ্যাক্সেস কন্ট্রোল (RBAC) টেস্ট**:
   - সাধারণ ইউজার `GET /users/admin/metrics` হিট করলে যেন অবধারিতভাবে `403 Forbidden` পায়।
   - সঠিক অ্যাডমিন ক্রেডেনশিয়াল দিলে যেন স্ট্যাটাস `200 OK` সহ মেট্রিক্স ডেটা পায়।
2. **আইডি ওআর (IDOR) ট্রায়াঙ্গুলার প্রিভেনশন টেস্ট**:
   - **কেস ১ (মালিক নিজে)**: User A (id=1) তার নিজের প্রোফাইল `PUT /users/1` আপডেট করলে $\rightarrow$ `200 OK`।
   - **কেস ২ (আক্রমণকারী)**: User A (id=1) যখন অন্য ইউজার User B (id=2)-এর প্রোফাইল `PUT /users/2` মিউটেশনের চেষ্টা করে $\rightarrow$ সাথে সাথে `403 Forbidden`।
   - **কেস ৩ (অ্যাডমিন ওভাররাইড)**: Admin User অন্য যেকোনো ইউজার User B (id=2)-এর প্রোফাইল `PUT /users/2` আপডেট করলে $\rightarrow$ `200 OK`।
3. **রিকোয়েস্ট-স্কোপড DAG এক্সিকিউশন স্পাই টেস্ট**:
   - একই এন্ডপয়েন্টে যখন `require_user_ownership` এবং অন্যান্য সাব-ডিপেনডেন্সি একই সাথে ইনজেক্টেড থাকে, তখন আসল সার্ভিস মেথড যেন পুরো HTTP রিকোয়েস্টে **কঠোরভাবে মাত্র একবার** এক্সিকিউট হয় (`assert call_count == 1`)।

---

## ৮. সিনিয়র আর্কিটেক্ট ইন্টারভিউ কিউএ (Staff-Level Interview Q&A)

### প্রশ্ন ১: FastAPI-তে সাধারণ ফাংশন ডিপেনডেন্সির বদলে প্যারামিটারাইজড ক্লাস (`RoleChecker`) ব্যবহার করার সুবিধা কী?
**উত্তর**:  
যদি আমরা সাধারণ ফাংশন ব্যবহার করি, তবে প্রতিটি রোলের জন্য আলাদা ফাংশন লিখতে হয় (`require_admin`, `require_manager`, `require_enterprise`)। অথবা যদি কম্বাইন্ড পারমিশন লাগে (`require_admin_or_manager`), তবে ফাংশনের সংখ্যা জ্যামিতিক হারে বাড়তে থাকে।  
কিন্তু পাইথনের `__call__` মেথডযুক্ত ক্লাস ব্যবহার করলে কনস্ট্রাক্টরে প্যারামিটার পাস করে রানটাইমে কাস্টমাইজড গার্ড ইন্সট্যান্স তৈরি করা যায়। যেমন: `RoleChecker([UserRole.ADMIN, UserRole.MANAGER])`। এটি কোড ডুপ্লিকেশন শূন্যে নামিয়ে আনে এবং ওপেন-ক্লোজড প্রিন্সিপল (OCP) নিশ্চিত করে।

### প্রশ্ন ২: FastAPI-র `Depends(use_cache=True)` কীভাবে মেমোরি লিক রোধ করে এবং এর লাইফসাইকেল কী?
**উত্তর**:  
FastAPI-এর `use_cache=True` কোনো গ্লোবাল ক্যাশ নয়। এটি **Request-Scoped Cache**। অর্থাৎ একটি নির্দিষ্ট HTTP রিকোয়েস্ট যখন শুরু হয়, তখন একটি অভ্যন্তরীণ ডিকশনারি `async_exit_stack` এবং ডিপেনডেন্সি ক্যাশ হিসেবে ইনিশিয়ালাইজ হয়। রিকোয়েস্টের ভেতর যেকোনো সাব-ডিপেনডেন্সি প্রথমবার এক্সিকিউট হওয়ার পর তার ফলাফল এই ডিকশনারিতে জমা থাকে। রিকোয়েস্টের রেসপন্স ক্লায়েন্টের কাছে ডেলিভার হওয়া মাত্রই পুরো রিকোয়েস্ট কনটেক্সট এবং তার অভ্যন্তরীণ ক্যাশ ডিকশনারি পাইথনের গারবেজ কালেক্টর দ্বারা রিলিজ হয়ে যায়। তাই এখানে কোনো মেমোরি লিক হওয়ার সুযোগ নেই।

### প্রশ্ন ৩: IDOR প্রতিরোধে ডাটাবেজ লেভেলে ফিল্টার করার চেয়ে ডিপেনডেন্সি লেভেলে গার্ড বসানো কেন শ্রেয়?
**উত্তর**:  
ডাটাবেজ লেভেলে ফিল্টার করলে (`SELECT * FROM users WHERE id = :user_id AND owner_id = :current_user_id`), কুয়েরি এক্সিকিউট হওয়ার আগে সিস্টেম জানতে পারে না ইউজার অ্যাক্সেস পাওয়ার যোগ্য কিনা। এর ফলে ডেটাবেজ কানেকশন অপচয় হয়।  
অপরদিকে ডিপেনডেন্সি লেভেলে গার্ড বসানোর ফলে:
1. রাউটার এবং সার্ভিস কোডে কোনো অতিরিক্ত ফিল্টারিং লজিক লিখতে হয় না (Clean Architecture)।
2. রিকোয়েস্ট সার্ভিস বা ডেটাবেজ লেয়ার পর্যন্ত পৌঁছানোর আগেই HTTP বাউন্ডারিতেই `403 Forbidden` হয়ে ড্রপ হয়, যা ডেটাবেজের সিপিইউ ও কানেকশন পুলকে রক্ষা করে।

---

## ৯. শিক্ষানবিস আর্কিটেক্টের চেকলিস্ট ও টেকঅ্যাওয়ে (Operational Checklist)

- [x] **Zero Imperative Security in Handlers**: রাউটার হ্যান্ডলারের ভেতরে কখনো `if user.role != "admin"` লিখবেন না; সর্বদা ডিক্লেয়ারেটিভ `Depends(RoleChecker(...))` ব্যবহার করুন।
- [x] **Guard All Mutating Endpoints with Ownership**: যেকোনো `PUT`, `PATCH`, বা `DELETE` এন্ডপয়েন্ট যা পাথ প্যারামিটারে রিসোর্স আইডি গ্রহণ করে, সেখানে অবশ্যই `require_user_ownership` জাতীয় গার্ড ইনজেক্ট করুন।
- [x] **Use `frozenset` for Role Guards**: প্যারামিটারাইজড ক্লাস গার্ডের কনস্ট্রাক্টরে রোল কালেকশনকে ইমিউটেবল `frozenset` হিসেবে সেভ করুন যাতে রানটাইমে $\mathcal{O}(1)$ মেম্বারশিপ স্পিড নিশ্চিত থাকে।
- [x] **Beware of Self-Referencing Overrides**: টেস্টিংয়ের সময় `app.dependency_overrides` কনফিগার করার সময় ওভাররাইডিং ফাংশনে যেন একই ডিপেনডেন্সির কল না থাকে, অন্যথায় `RecursionError` ঘটবে।
- [x] **Path Precedence Awareness**: লিটারাল রুটগুলো (যেমন: `GET /users/admin/metrics`) সর্বদা ডাইনামিক প্যারামিটারাইজড রুটের (`GET /users/{user_id}`) পূর্বে ডিক্লেয়ার করুন।
