# RCA: Day 26 - Binary Search Range Boundary Invariants, Test Payload Schema Incomplete, and Route Precedence

- **Date**: 2026-09-09
- **Role**: Junior Apprentice Backend Engineer
- **Lead Architect & Mentor**: User
- **Topic**: Binary Search Bisection Invariants, Two-Pointer Converging Search, Test DTO Invariant Validation, and FastAPI Literal Route Precedence

---

## 1. Trigger

During Day 26 test suite execution and endpoint integration:
1. `test_filter_users_by_age_success` failed with:
   ```text
   AssertionError: assert 422 in (201, 409)
   + where 422 = <Response [422 Unprocessable Entity]>.status_code
   ```
2. Running `mypy --strict app tests/test_binary_search_and_two_pointer.py` reported:
   ```text
   tests\test_binary_search_and_two_pointer.py:168: error: Argument 1 to "float" has incompatible type "object"; expected "str | Buffer | SupportsFloat | SupportsIndex"  [arg-type]
   ```
3. Architectural Hazard Analysis: Risk of route parameter shadowing if `GET /users/filter/by-age` is positioned after `GET /users/{user_id}`, causing FastAPI to match the literal segment `"filter"` against `{user_id: int}` and fail with HTTP 422.

---

## 2. Faulty Code / Pattern

### Issue A: Omitting Transient Invariant Fields in Test Client Payloads
```python
# Faulty test payload in tests/test_binary_search_and_two_pointer.py
test_users = [
    {"email": "age19@example.com", "username": "young_coder", "password": "Password123!", "age": 19},
]
res = client.post("/users/", json=u)
assert res.status_code in (201, 409)
```
In Day 05, we codified strict Pydantic v2 cross-field invariants in `UserCreate`:
```python
@model_validator(mode="after")
def validate_cross_field_invariants(self) -> Self:
    if self.password != self.password_confirm:
        raise ValueError("Passwords do not match.")
    ...
```
Because `password_confirm` is a required transient field on `UserCreate`, omitting it from test registration payloads causes Pydantic to reject the request at the schema boundary with `422 Unprocessable Entity` before reaching the repository.

### Issue B: Untyped Heterogeneous Dict Elements Under Strict Mypy
```python
users = [
    {"username": "alice", "score": 10},
    {"username": "bob", "score": 25},
]
# Mypy infers users as list[dict[str, object]]
match = two_pointer_pair_search(users, target=65.0, key_func=lambda u: float(u["score"]))
```
Under `mypy --strict`, indexing an inferred `dict[str, object]` yields type `object`. Passing an arbitrary `object` to Python's `float()` constructor violates mypy's type signature, which expects `SupportsFloat` or primitive numeric types.

### Issue C: Route Path Shadowing Hazard
```python
# If declared in this order:
@router.get("/{user_id}", response_model=UserResponse)
async def get_user_by_id(user_id: int = Path(...)):
    ...

@router.get("/filter/by-age", response_model=list[UserResponse])
async def filter_users_by_age(...):
    ...
```
FastAPI evaluates registered endpoints sequentially in declaration order. If a parameterized route `/{user_id}` is declared first, a request to `/users/filter/by-age` matches the pattern with `user_id = "filter"`. Because `user_id` is constrained to `int`, FastAPI raises HTTP 422 instead of routing to `/filter/by-age`.

---

## 3. Root Cause

1. **Schema Boundary Contract Enforcement**: Our application enforces zero garbage data at the application perimeter. Tests interacting with live HTTP endpoints via `TestClient` must fully satisfy the DTO contract (`UserCreate`), including transient confirmation fields (`password_confirm`).
2. **Strict Mypy Object Invariance**: Python's type inference on literal heterogeneous dictionaries defaults values to `object`. Lambda projections over dictionary keys require explicit casting or explicit typed containers (`dict[str, Any]`, dataclasses) to satisfy strict type checkers.
3. **Route Declaration Precedence in Starlette/FastAPI Router**: Path matching follows first-match semantics. Dynamic path parameters (`{user_id}`) must never precede static/literal route paths (`/filter/by-age`, `/autocomplete`, `/me`).

---

## 4. Resolution

### Solution A: Supply Full DTO Invariant Payloads in Tests
```python
test_users = [
    {
        "email": "age19@example.com",
        "username": "young_coder",
        "password": "Password123!",
        "password_confirm": "Password123!",
        "age": 19,
    },
    ...
]
```

### Solution B: Explicit Typing and Casting for Strict Mypy Compliance
```python
users: list[dict[str, Any]] = [
    {"username": "alice", "score": 10},
    {"username": "bob", "score": 25},
]
match = two_pointer_pair_search(
    users,
    target=65.0,
    key_func=lambda u: float(int(u["score"])),
)
```

### Solution C: Enforce Literal Route Declaration Precedence
In `app/routers/user_router.py`:
```python
# 1. Literal search/filter endpoints declared first
@router.get("/filter/by-age", response_model=list[UserResponse])
async def filter_users_by_age(...):
    ...

# 2. Dynamic path parameter routes declared afterwards
@router.get("/{user_id}", response_model=UserResponse)
async def get_user_by_id(...):
    ...
```

---

## 5. Permanent Prevention Rules

1. **DTO Contract Completeness in Test Suites**: Any test calling `client.post("/users/")` must supply valid `password` and `password_confirm` matching pairs to satisfy model invariants.
2. **Explicit Type Annotation for Test Data Structures**: In all test suites checked with `mypy --strict`, explicitly annotate heterogeneous dictionaries as `list[dict[str, Any]]` and use explicit primitive converters (`float(int(...))`).
3. **Strict Literal-Before-Dynamic Route Ordering**: Every literal endpoint (e.g. `/filter/...`, `/autocomplete`, `/search`, `/export`) on an entity router must always be registered before dynamic entity ID paths (`/{entity_id}`).
