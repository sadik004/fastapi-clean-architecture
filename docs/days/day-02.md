# Day 02: Pydantic v2 Strict Models, DTOs & Request Validation Pipelines

**Date**: 2026-09-08  
**Role**: Junior Apprentice Backend Engineer  
**Lead Architect & Mentor**: User

---

## 1. Concepts Covered Today
- **Pydantic v2 Field Constraints**: Utilizing `Field()` with granular rules:
  - `username`: Enforced length boundaries (`min_length=3`, `max_length=50`) and regex pattern `^[a-zA-Z0-9_]+$` compiled into Rust-speed `pydantic-core`.
  - `email`: Strict `EmailStr` RFC validation.
  - `age`: Numeric range constraints (`ge=18`, `le=120`) preventing impossible or underage profiles.
  - `role`: Type-safe string enum `UserRole` (`user`, `admin`).
- **Strict DTO & Request/Response Separation**:
  - `UserCreate`: Accepts plain-text registration credentials (`password` with `min_length=8`, `max_length=128`).
  - `UserProfileUpdate`: Accepts partial, optional profile adjustments (`username`, `age`).
  - `UserResponse`: Strict serialization contract that never exposes sensitive domain fields (`password`, `password_hash`).
- **Entity Hydration with `ConfigDict(from_attributes=True)`**: Enabled `UserResponse` to seamlessly serialize from internal dataclass entities (`UserEntity`) while enforcing response filtering.
- **Thin Router Boundary**: Zero manual validation, filtering, or JSON payload munging inside router endpoint handlers. All input validation occurs automatically in FastAPI's request pipeline via Pydantic v2.

---

## 2. Architectural Decisions Made
- **Sensitive Field Isolation**: `password_hash` lives strictly within internal domain entities (`UserEntity`) and persistence storage. It is never declared on or mapped to `UserResponse`.
- **Partial Updates via `PATCH /users/{user_id}`**: Integrated `UserProfileUpdate` into the router and service layer. Uniqueness constraints for modified usernames are verified in $\mathcal{O}(1)$ time against the repository's inverted hash index.
- **Strict Enum Typing**: Implemented `UserRole(str, Enum)` ensuring valid OpenAPI schema generation, string serialization compatibility, and rejection of invalid role assignments.

---

## 3. DSA Time & Space Complexity Enforced
- **Input Validation Complexity**: $\mathcal{O}(1)$ per request. Length, regex pattern matching, and numeric range checks are executed in compiled C/Rust via `pydantic-core`.
- **Mutable Reference Defense**: All default values are primitive immutable types or explicit enumerations, eliminating mutable default reference leak traps.
- **Profile Update Inverted Index Maintenance**: $\mathcal{O}(1)$ update time. When a username is changed, the old handle is evicted from `_username_index` and the new handle is mapped in constant time.

---

## 4. Summary of Test Results & Quality Gates
- **Pytest**: 42 passed in 0.47s (`100%` pass rate).
  - Schema unit test suite (`tests/test_schemas.py`): 25 passed covering valid instantiations, boundary conditions, regex rejections, under-age/over-age inputs, and sensitive field exclusion assertions.
  - User integration test suite (`tests/test_user.py`): 17 passed covering HTTP 201 creation, HTTP 422 validation rejections, HTTP 409 collisions, HTTP 404 lookups, and PATCH profile updates.
- **Ruff**: `All checks passed!` Zero lint warnings or style violations across `app/` and `tests/`.
- **Mypy**: `Success: no issues found in 15 source files` using `mypy --strict app tests`.

---

## 5. Suggested Git Commit
```bash
git commit -m "feat(day-02): enforce strict Pydantic v2 schemas, DTO boundaries, and field validation"
```
