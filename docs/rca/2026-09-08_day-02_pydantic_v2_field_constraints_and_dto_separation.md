# RCA: Day 02 Sensitive Credential Leakage & DTO Boundary Violations

- **Date**: 2026-09-08
- **Trigger**: Schema design audit during Pydantic v2 migration.

---

## 1. Incident 1: Sensitive Credential Leakage in Single Monolithic Model

### Faulty Code / Pattern
```python
# app/schemas/user.py
class UserModel(BaseModel):
    id: Optional[int] = None
    email: EmailStr
    username: str
    password: str
    password_hash: Optional[str] = None  # Anti-pattern: shared model leaks secret
```

### Root Cause
Using a single monolithic model for both input validation and output serialization causes sensitive internal attributes (like `password_hash` or transient raw credentials) to be exposed in HTTP response bodies, creating a severe security vulnerability.

### Resolution
Enforced strict request/response DTO separation. `UserCreate` accepts credentials, while `UserResponse` explicitly defines only public attributes and uses `model_config = ConfigDict(from_attributes=True)`:
```python
# app/schemas/user.py
class UserCreate(UserBase):
    password: str = Field(..., min_length=8, max_length=128)

class UserResponse(UserBase):
    id: int
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)
    # password and password_hash are permanently omitted
```

---

## 2. Incident 2: Mutable Default Argument Anti-Pattern

### Faulty Code / Pattern
```python
# app/schemas/user.py
class UserProfileUpdate(BaseModel):
    tags: list[str] = []  # Anti-pattern: mutable default list
```

### Root Cause
In Python, mutable default arguments (e.g. `[]` or `{}`) share state across instances and requests, causing subtle cross-request contamination bugs and thread-safety violations.

### Resolution
Enforced immutable defaults or `default_factory`:
```python
tags: list[str] = Field(default_factory=list)
```

---

## Permanent Prevention Rules Added to SKILL.md
1. **Strict Request/Response Model Separation**: Never return domain models or input payloads directly to clients. Every response must pass through an explicit `ResponseModel`.
2. **Immutable Defaults Only**: Default values must be primitives or generated via `Field(default_factory=...)`.
