# RCA: Day 05 `model_dump()` Allocation Overhead & Transient Validation Field Leakage

- **Trigger**: Cross-field invariant code review during Day 05 implementation.

---

## 1. Incident 1: `self.model_dump()` Allocation Inside `@model_validator`

### Faulty Code / Pattern
```python
# app/schemas/user.py
@model_validator(mode="after")
def check_passwords(self) -> Self:
    # Anti-pattern: serializing the entire model to a dictionary inside a validator
    data = self.model_dump()
    if data["password"] != data["password_confirm"]:
        raise ValueError("Passwords do not match")
    return self
```

### Root Cause
Calling `self.model_dump()` within `@model_validator(mode='after')` recursively serializes all fields into a new Python dictionary on the heap. This causes unnecessary memory allocations, triggers garbage collection overhead, and degrades throughput on high-frequency registration endpoints.

### Resolution
Accessed validated attributes directly on `self` with `Self` typing, avoiding intermediate object creation:
```python
@model_validator(mode="after")
def validate_cross_field_invariants(self) -> Self:
    if self.password != self.password_confirm:
        raise ValueError("password and password_confirm do not match.")
    return self
```

---

## 2. Incident 2: Transient Validation Artifacts Leaking to Domain Entities

### Faulty Code / Pattern
```python
# app/services/user_service.py
def register_user(self, payload: UserCreate):
    # Anti-pattern: passing entire payload dump including transient validation fields
    user = self._repo.create(**payload.model_dump())
```

### Root Cause
`password_confirm` is purely a transient HTTP input validation constraint. If passed blindly via `payload.model_dump()` into `UserRepository.create()` or `UserEntity`, transient fields leak into databases, entities, or output response objects.

### Resolution
Explicitly filtered transient fields. `password_confirm` is verified at the Pydantic boundary and dropped before domain entities or persistence storage are touched:
```python
# app/services/user_service.py
user = self._repo.create(
    email=payload.email,
    username=payload.username,
    password_hash=hashed_password,
    age=payload.age,
    role=payload.role,
    company_name=payload.company_name,
)
```

---

## Permanent Prevention Rules Added to SKILL.md
1. **Direct Attribute Access in Model Validators**: Never call `self.model_dump()` inside `@model_validator(mode='after')`. Inspect typed attributes directly on `self`.
2. **Strict Transient Field Isolation**: Confirmation fields (`password_confirm`) must never exist on domain entities, database models, or response DTOs.
