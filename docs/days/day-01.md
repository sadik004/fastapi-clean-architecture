# Day 01: 90-Day Architecture Initialization, 3-Tier Boundaries & O(1) In-Memory Repository

**Date**: 2026-09-08  
**Role**: Junior Apprentice Backend Engineer  
**Lead Architect & Mentor**: User

---

## 1. Concepts Covered Today
- **3-Tier Clean Architecture**: Strictly segregating Router (`app/routers/`), Service (`app/services/`), and Repository (`app/repositories/`) layers.
- **Domain Decoupling**: Raising domain-specific exceptions (`UserAlreadyExistsException`, `UserNotFoundException`) inside the service layer, keeping business logic agnostic of HTTP transport.
- **Pydantic v2 Request & Response Schemas**: Using `UserCreate` for input validation and `UserResponse` with `ConfigDict(from_attributes=True)` for output serialization.
- **Dependency Injection**: Wiring the repository and service via FastAPI's `Depends` and `Annotated` parameters in the router.
- **Test-Driven Rigor**: Writing isolation fixtures using `Generator[None, None, None]` to clear in-memory state before and after each test.

---

## 2. Architectural Decisions Made
- **Single Evolving Codebase Enforced**: No standalone `day01/` folder created. All code lives inside unified packages under `app/` and `tests/`.
- **Inverted Hash Indexes in Repository**: Maintained `_store: dict[int, UserEntity]` alongside `_email_index: dict[str, int]` and `_username_index: dict[str, int]` inside `InMemoryUserRepository`.
- **Domain-to-HTTP Exception Mapping**: The router explicitly catches domain exceptions and translates them to HTTP `409 Conflict` and `404 Not Found`.

---

## 3. DSA Time & Space Complexity Enforced
- **Lookup by ID (`get_by_id`)**: $\mathcal{O}(1)$ time complexity using primary dictionary hash table.
- **Email Uniqueness Check (`get_by_email`)**: $\mathcal{O}(1)$ time complexity using the email inverted index.
- **Username Uniqueness Check (`get_by_username`)**: $\mathcal{O}(1)$ time complexity using the username inverted index.
- **User Creation (`create`)**: $\mathcal{O}(1)$ amortized insertion into primary storage and secondary index dictionaries.
- **Forbidden Pattern Avoided**: Zero $\mathcal{O}(n)$ linear scans across user lists to verify uniqueness or look up by ID.

---

## 4. Summary of Test Results & Quality Gates
- **Pytest**: 8 passed in 0.44s (`100%` pass rate).
  - Health check test (`/health`).
  - User creation success (`201 Created`).
  - Duplicate email collision rejection (`409 Conflict`).
  - Duplicate username collision rejection (`409 Conflict`).
  - Invalid email validation (`422 Unprocessable Entity`).
  - Lookup by ID success (`200 OK`).
  - Lookup by ID non-existent (`404 Not Found`).
  - List users verification.
- **Ruff**: `All checks passed!` Zero lint errors across `app/` and `tests/`.
- **Mypy**: `Success: no issues found in 14 source files` with `mypy --strict app tests`.

---

## 5. Suggested Git Commit
```bash
git commit -m "feat(day-01): establish 3-tier clean architecture with O(1) in-memory user service"
```
