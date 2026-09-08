# RCA: Day 06 Path & Query Validation, Domain Exception Typing, and OpenAPI 3.1 Schema Traps

- **Trigger**: Test failure & Mypy `--strict` type errors during Day 06 implementation.

---

## 1. Incident 1: Domain Exception Identifier Incompatibility with Mypy Strict

### Faulty Code / Pattern
```python
# app/core/exceptions.py
class UserNotFoundException(DomainException):
    def __init__(self, user_id: int) -> None:
        super().__init__(f"User with ID {user_id} was not found.")

# app/services/user_service.py
def get_user_by_username(self, username: str) -> UserEntity:
    user = self._repo.get_by_username(username)
    if user is None:
        raise UserNotFoundException(f"User with username '{username}' not found.")  # Mypy error!
```

### Root Cause
`UserNotFoundException` was originally built assuming only integer `user_id` lookups existed (from Days 1–3). When `get_user_by_username` was introduced, passing a string or formatted message violated the strict positional type contract (`expected int, got str`).

### Resolution
Updated `UserNotFoundException` to accept polymorphic identification (`identifier: int | str | None = None, *, user_id: int | None = None`) with automatic message formatting, preserving full backward compatibility for existing callers:
```python
# app/core/exceptions.py
class UserNotFoundException(DomainException):
    """Raised when a requested user does not exist."""

    def __init__(
        self,
        identifier: int | str | None = None,
        *,
        user_id: int | None = None,
    ) -> None:
        target = user_id if user_id is not None else identifier
        if isinstance(target, int):
            message = f"User with ID {target} was not found."
        elif isinstance(target, str):
            message = f"User with username '{target}' was not found."
        else:
            message = "User was not found."
        super().__init__(message)
```

---

## 2. Incident 2: OpenAPI 3.1.0 Nullable Schema Structure Trap

### Faulty Code / Pattern
```python
# tests/test_path_query_validation.py
search_param = next(p for p in list_params if p["name"] == "search")
assert search_param["schema"]["minLength"] == 2  # KeyError: 'minLength'
```

### Root Cause
Under FastAPI with Pydantic v2 and OpenAPI 3.1.0, optional query parameters (e.g., `search: Optional[str] = None`) are serialized as nullable union schemas using `anyOf`:
```json
{
  "anyOf": [
    {
      "type": "string",
      "minLength": 2,
      "maxLength": 50,
      "pattern": "^[a-zA-Z0-9_ ]+$"
    },
    {
      "type": "null"
    }
  ]
}
```
Attempting direct dictionary key access on `search_param["schema"]["minLength"]` fails because the constraints reside inside the first variant of `anyOf`.

### Resolution
Inspected the schema by checking for `anyOf` before asserting constraints:
```python
search_schema = search_param["schema"].get("anyOf", [search_param["schema"]])[0]
assert search_schema["minLength"] == 2
assert search_schema["maxLength"] == 50
assert search_schema["pattern"] == "^[a-zA-Z0-9_ ]+$"
```

---

## 3. Incident 3: Attempting to Update Non-Existent Schema Field via HTTP Patch

### Faulty Code / Pattern
```python
# tests/test_path_query_validation.py
client.patch(f"/users/{first_id}", json={"is_active": False})
```

### Root Cause
`UserProfileUpdate` strictly guards user-updatable fields (`full_name`, `bio`, `company_name`, `phone_number`, etc.). `is_active` is a domain-controlled account state and was intentionally excluded from `UserProfileUpdate`. Sending `{"is_active": False}` was ignored by Pydantic (extra fields ignored/not assigned to entity), causing the user to remain active.

### Resolution
Directly mutated the test entity via the repository layer for the specific repository-level filtering test:
```python
first_id_val = seeded[0]["id"]
assert isinstance(first_id_val, int)
repo = get_user_repository()
user = repo.get_by_id(first_id_val)
assert user is not None
user.is_active = False
```

---

## Permanent Prevention Rules Added to SKILL.md
1. **Polymorphic Domain Exceptions**: When domain entities support multiple lookup dimensions (ID, email, username), domain exceptions must cleanly accept either identifier type without breaking strict type signatures.
2. **OpenAPI 3.1 Schema Awareness**: Always account for `anyOf: [..., {"type": "null"}]` when programmatically introspecting nullable parameters in OpenAPI 3.1.
3. **DTO Responsibility Integrity**: Never assume an administrative or domain state field (`is_active`, `created_at`, `role`) is mutable through standard profile update endpoints unless explicitly defined in the update schema.
