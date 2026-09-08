# Day 05: Pydantic v2 Model-Level Validation (Cross-Field Invariants with @model_validator(mode='after'))

**Date**: 2026-09-08  
**Role**: Junior Apprentice Backend Engineer  
**Lead Architect & Mentor**: User

---

## 1. Concepts Covered Today
- **Model-Level Validation (`@model_validator(mode='after')`)**:
  - Validates cross-field dependencies and business invariants where field validity depends on other fields in the payload.
  - Typed `def validate_cross_field_invariants(self) -> Self:` using `typing.Self`.
  - Direct attribute access on `self` (`self.password`, `self.password_confirm`, `self.username`, `self.role`, `self.company_name`) to eliminate intermediate dictionary conversion allocations.
- **Cross-Field Invariants Enforced**:
  - **Password Confirmation Match**: `password` and `password_confirm` must match identically.
  - **Credential Integrity**: Password must not contain the username (case-insensitive substring check).
  - **Conditional Role Requirements**: `role == UserRole.ENTERPRISE` strictly requires `company_name` to be present and non-empty.
- **Transient DTO Cleansing**:
  - `password_confirm` exists exclusively on `UserCreate` as an input validation artifact.
  - It is completely omitted from domain entities (`UserEntity`), internal repositories, and output response schemas (`UserResponse`).

---

## 2. Architectural Decisions Made
- **Clean Schema Boundary Enforcement**: All cross-field business logic is encapsulated within `app/schemas/user.py`. Router handlers and `UserService` are completely decoupled from raw validation conditions (e.g. zero `if password != confirm` in services or routers).
- **Direct Attribute Access vs Dictionary Serialization**: Refrained from invoking `self.model_dump()` inside validators; directly accessing typed attributes on `self` avoids redundant dictionary copies and preserves execution speed.
- **Enterprise Domain Extension**: Added `UserRole.ENTERPRISE` and `company_name` across `UserBase`, `UserCreate`, `UserUpdate`, `UserProfileUpdate`, and `UserEntity`.

---

## 3. DSA Time & Space Complexity Enforced
- **Cross-Field Validation**: Strictly **$\mathcal{O}(L)$** time complexity where $L$ is the combined length of `password` and `username` (string equality and substring scan).
- **Space Complexity**: **$\mathcal{O}(1)$** auxiliary space. Inspecting attributes directly on `self` avoids memory allocations from `model_dump()`.

---

## 4. Summary of Test Results & Quality Gates
- **Pytest**: 97 passed in 0.69s (`100%` pass rate).
  - `tests/test_model_validators.py`: 9 unit tests verifying matching password success, password mismatch error (422), username-in-password rejection (case-insensitive), Enterprise role without `company_name` rejection, Enterprise role with whitespace rejection, Enterprise role with valid `company_name` success, standard user without `company_name` success, and transient field exclusion assertion.
  - `tests/test_user.py`: 28 integration tests verifying endpoint status codes for cross-field violations and transient field exclusions.
  - `tests/test_field_validators.py`: 23 tests passing.
  - `tests/test_schemas.py`: 25 tests passing.
  - `tests/test_user_crud.py`: 5 tests passing.
  - `tests/test_user_repository.py`: 7 tests passing.
- **Mypy**: `Success: no issues found in 19 source files` using `mypy --strict app tests`.
- **Ruff**: `All checks passed!` across `app/` and `tests/`.

---

## 5. Suggested Git Commit
```bash
git commit -m "feat(day-05): enforce cross-field invariants using Pydantic v2 model_validator"
```
