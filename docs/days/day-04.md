# Day 04: Pydantic v2 Advanced Validation (Custom @field_validator, Field Normalization & Business Cleaning)

**Date**: 2026-09-08  
**Role**: Junior Apprentice Backend Engineer  
**Lead Architect & Mentor**: User

---

## 1. Concepts Covered Today
- **Pydantic v2 Validator Modes**:
  - `mode='before'`: Raw input transformation before type casting or schema constraints execute. Applied for trimming whitespace, lowercase normalization, internal multi-space collapsing, and XSS HTML tag stripping.
  - `mode='after'`: Invariant enforcement after type casting. Applied for business domain rules (prohibiting consecutive underscores, checking reserved system usernames, E.164 phone regex matching).
- **Perimeter Sanitization & Defense in Depth**:
  - `username`: Auto-normalized to lowercase, whitespace trimmed, verified for no consecutive underscores (`__`), and checked against reserved system handles (`admin`, `root`, `system`, etc.).
  - `full_name`: Collapsed repeated internal spaces to single spaces and converted to Title Case.
  - `phone_number`: Optional field strictly validated against international E.164 standards (`^\+[1-9]\d{1,14}$`).
  - `bio`: HTML tags stripped via pre-compiled regex to eliminate XSS payload vectors at the API boundary.
- **Service & Router Isolation**:
  - Routers and Services are completely freed from raw string-cleaning logic (`.strip()`, `.lower()`, regex parsing). The data reaching `UserService` is guaranteed 100% normalized, type-safe, and invariant-compliant.

---

## 2. Architectural Decisions Made
- **Module-Level Pre-Compilation & Constants**:
  - Pre-compiled regexes (`RE_E164_PHONE`, `RE_HTML_TAGS`) at module scope to eliminate per-request regex compilation overhead.
  - Defined `RESERVED_USERNAMES = frozenset(...)` at module scope to guarantee $\mathcal{O}(1)$ keyword membership checks.
- **Shared Normalization across DTOs**:
  - Attached custom field validators consistently across `UserBase`, `UserCreate`, `UserUpdate`, and `UserProfileUpdate` so that creation and update operations maintain identical invariants.
- **Repository Entity Synchronization**:
  - Updated `UserEntity`, `UserRepositoryProtocol`, and `InMemoryUserRepository` to store and update `full_name`, `phone_number`, and `bio`.

---

## 3. DSA Time & Space Complexity Enforced
- **Reserved Word Membership Lookup**: Strictly **$\mathcal{O}(1)$** time complexity via `frozenset` hashing, avoiding $\mathcal{O}(m)$ linear list scans.
- **String Sanitization**: Strictly **$\mathcal{O}(k)$** time complexity where $k$ is string length (linear pass for trimming, splitting, Title Casing, and regex substitution).
- **Regex Execution**: Pre-compiled Deterministic Finite Automaton (DFA) matching in $\mathcal{O}(k)$ time per request. Zero per-request compilation cost.

---

## 4. Summary of Test Results & Quality Gates
- **Pytest**: 84 passed in 0.62s (`100%` pass rate).
  - `tests/test_field_validators.py`: 12 unit tests verifying raw input normalization, consecutive underscore rejection, reserved username rejection (including mixed case and whitespace), Title Case space-collapsing, E.164 phone formats, and XSS tag removal.
  - `tests/test_user.py`: 24 integration tests including end-to-end sanitized input round-tripping, reserved name 422 rejections, and invalid phone number 422 rejections.
  - `tests/test_schemas.py`: 25 tests passing.
  - `tests/test_user_crud.py`: 5 tests passing.
  - `tests/test_user_repository.py`: 7 tests passing.
- **Mypy**: `Success: no issues found in 18 source files` using `mypy --strict app tests`.
- **Ruff**: `All checks passed!` across `app/` and `tests/`.

---

## 5. Suggested Git Commit
```bash
git commit -m "feat(day-04): add Pydantic v2 custom field validators, normalization, and XSS sanitization"
```
