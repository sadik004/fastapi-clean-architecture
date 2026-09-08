# Day 06: FastAPI Path & Query Validation (Path(), Query(), and Custom Regex Constraints)

**Date**: 2026-09-08  
**Role**: Junior Apprentice Backend Engineer  
**Lead Architect & Mentor**: User

---

## 1. Concepts Covered Today
- **Declarative Path Validation (`fastapi.Path`)**:
  - Enforced bounds on identifier parameters: `user_id: int = Path(..., ge=1, le=2_147_483_647, description="...")`.
  - Rejection of non-positive integers (`<= 0`), out-of-bound values, and non-integer inputs with automatic HTTP 422 Unprocessable Entity responses.
  - Implemented `GET /users/by-username/{username}` with regex constraints: `Path(..., min_length=3, max_length=50, pattern=r"^[a-z0-9_]+$", description="...")`.
- **Declarative Query Validation (`fastapi.Query`)**:
  - Pagination limits and offsets: `limit: int = Query(default=10, ge=1, le=100)` and `offset: int = Query(default=0, ge=0)`.
  - Query parameter filtering: `role: Optional[UserRole] = Query(default=None)`, `search: Optional[str] = Query(default=None, min_length=2, max_length=50, pattern=r"^[a-zA-Z0-9_ ]+$")`, and `is_active: Optional[bool] = Query(default=None)`.
  - Rejection of invalid query payloads (`limit=0`, `limit=101`, `offset=-1`, `search="a"`, `search="invalid@#$"`, `role="unknown"`) with HTTP 422.
- **OpenAPI 3.1 Parameter Metadata**:
  - Parameter constraints, lengths, regular expressions, and documentation automatically reflected in `/openapi.json` without manual schema plumbing.

---

## 2. Architectural Decisions Made
- **Clean Transport Boundary Enforcement**: Zero imperative parameter validation loops or manual checks (e.g. `if limit > 100`) inside endpoint handlers. FastAPI declarative parameter definitions enforce transport invariants before route logic executes.
- **Repository Protocol & Service Decoupling**: Extended `UserRepositoryProtocol.list_all` and `UserService.list_users` with optional filter arguments (`role`, `search`, `is_active`).
- **Fast-Path Pagination Optimization**: In `InMemoryUserRepository.list_all`, when all filter criteria are `None`, results are sliced directly (`list(self._store.values())[offset : offset + limit]`) without creating an intermediate filtered list.
- **Single-Pass Filtering**: When filters are provided, filtering executes in a single $\mathcal{O}(n)$ pass over repository entities, followed by bounded slice pagination.
- **Unified Domain Exception Handling**: Enhanced `UserNotFoundException` to cleanly accept either integer IDs (`user_id`) or string usernames/identifiers, maintaining full backwards compatibility across the application.

---

## 3. DSA Time & Space Complexity Enforced
- **Username Lookup**: Strictly **$\mathcal{O}(1)$** time via secondary inverted hash index (`_username_index`) in `InMemoryUserRepository`.
- **ID Lookup / Mutation**: Strictly **$\mathcal{O}(1)$** time via primary hash map storage (`_store`).
- **Unfiltered Pagination**: **$\mathcal{O}(\text{offset} + \text{limit})$** time and space for slicing stored values.
- **Filtered Pagination**: Single linear pass **$\mathcal{O}(n)$** time where $n$ is total stored entities, strictly avoiding nested loops or redundant passes.

---

## 4. Summary of Test Results & Quality Gates
- **Pytest**: 137 passed in 0.92s (`100%` pass rate).
  - `tests/test_path_query_validation.py`: 40 tests covering path bounds, username slug regex, query limits, search regex/lengths, role enums, boolean query parsing, filtering combinations, pagination slices, and OpenAPI parameter metadata introspection.
  - `tests/test_field_validators.py`: 23 tests passing.
  - `tests/test_model_validators.py`: 9 tests passing.
  - `tests/test_schemas.py`: 25 tests passing.
  - `tests/test_user.py`: 28 tests passing.
  - `tests/test_user_crud.py`: 5 tests passing.
  - `tests/test_user_repository.py`: 7 tests passing.
- **Mypy**: `Success: no issues found in 20 source files` using `mypy --strict app tests`.
- **Ruff**: `All checks passed!` across `app/` and `tests/`.

---

## 5. Suggested Git Commit
```bash
git commit -m "feat(day-06): enforce declarative Path and Query validation with regex and pagination bounds"
```
