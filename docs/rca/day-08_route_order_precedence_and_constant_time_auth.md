# RCA: Day 08 Route Order Precedence, Timing Attack Hazards, and Frozen Settings Typing

- **Trigger**: Routing collision (HTTP 422 on `/users/me`), timing vulnerability audit, and Mypy strict error on immutable settings verification.

---

## 1. Incident 1: Literal Route Collision with Path Parameter (`/users/me` vs `/users/{user_id}`)

### Faulty Code / Pattern
```python
# app/routers/user_router.py
@router.get("/{user_id}", response_model=UserResponse)
def get_user_by_id(user_id: int = Path(..., ge=1)): ...

@router.get("/me", response_model=UserResponse)
def get_current_user_profile(current_user: Annotated[UserEntity, Depends(get_current_user)]): ...
```

### Root Cause
FastAPI and Starlette match incoming request URL paths sequentially against registered routes in declaration order. When `/users/{user_id}` was declared before `/users/me`, an incoming request to `/users/me` matched the `{user_id}` parameter route first. FastAPI attempted to validate `"me"` as an integer (`int`), producing an immediate `422 Unprocessable Entity` validation error:
```json
{
  "detail": [
    {
      "type": "int_parsing",
      "loc": ["path", "user_id"],
      "msg": "Input should be a valid integer, unable to parse string as an integer",
      "input": "me"
    }
  ]
}
```

### Resolution
Always declare static/literal sub-paths (such as `/users/me`, `/users/export`, `/users/search`) **strictly before** dynamic parameterized paths (such as `/users/{user_id}`) in APIRouters:
```python
# app/routers/user_router.py
@router.get(
    "/me",
    response_model=UserResponse,
    status_code=status.HTTP_200_OK,
    summary="Get current authenticated user profile",
)
def get_current_user_profile(
    current_user: Annotated[UserEntity, Depends(get_current_user)],
) -> UserResponse:
    return UserResponse.model_validate(current_user)

@router.get(
    "/{user_id}",
    response_model=UserResponse,
    status_code=status.HTTP_200_OK,
    summary="Get user by ID",
)
def get_user_by_id(
    user_id: int = Path(..., ge=1, le=2_147_483_647),
    service: Annotated[UserService, Depends(get_user_service)] = None,
) -> UserResponse: ...
```

---

## 2. Incident 2: Timing Attack Vulnerability in Secret Comparison

### Faulty Code / Pattern
```python
# app/core/dependencies.py
if x_api_key == settings.admin_api_key:
    target_username = settings.admin_username
```

### Root Cause
Python's standard string equality operator (`==`) compares strings character-by-character from left to right and returns `False` immediately upon encountering the first mismatch. Over a network, an attacker can analyze millisecond/microsecond variations in response times to iteratively deduce the secret character by character (side-channel timing attack).

### Resolution
Enforce cryptographic constant-time comparison using `secrets.compare_digest(a, b)`:
```python
# app/core/dependencies.py
import secrets

if secrets.compare_digest(x_api_key, settings.admin_api_key):
    target_username = settings.admin_username
```

---

## 3. Incident 3: Mypy Strict Rejection on Mutating Frozen Settings in Tests

### Faulty Code / Pattern
```python
# tests/test_auth_dependencies.py
def test_settings_immutability():
    settings = get_settings()
    with pytest.raises(ValidationError):
        settings.debug = True  # Mypy: Cannot assign to field "debug" of immutable class "Settings"
```

### Root Cause
Because `Settings` is defined with `model_config = SettingsConfigDict(frozen=True)`, Mypy's strict type checker detects static assignment to a frozen Pydantic model and fails the build during compilation before the test can even execute.

### Resolution
Use `setattr` to test dynamic runtime immutability enforcement without triggering static type errors:
```python
# tests/test_auth_dependencies.py
def test_settings_immutability():
    settings = get_settings()
    with pytest.raises(ValidationError):
        setattr(settings, "debug", True)
```

---

## Permanent Prevention Rules
1. **Literal Paths Before Dynamic Parameters**: Always place literal endpoint paths (`/me`, `/stats`, `/filter`) before parameterized paths (`/{id}`) in FastAPI routers.
2. **Secrets Must Use `secrets.compare_digest`**: Never use `==` for API keys, passwords, bearer tokens, or HMAC signatures.
3. **Pydantic Immutability Tested via `setattr`**: When verifying frozen model constraints in tests, use `setattr` to satisfy static type checkers while guaranteeing runtime validation.
