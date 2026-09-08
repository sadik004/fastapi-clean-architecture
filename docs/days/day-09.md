# Day 09: Dependency Chaining, Sub-Dependencies & Parameterized Class Guards

**Date**: 2026-09-08  
**Role**: Junior Apprentice Backend Engineer  
**Lead Architect & Mentor**: User

---

## 1. Concepts Covered Today
- **Parameterized Callable Class Dependencies (`app/core/dependencies.py`)**:
  - Implemented `RoleChecker`: parameterized class guard configured via `__init__(self, allowed_roles: Sequence[UserRole | str], detail: str)` that internally compiles roles into an immutable `frozenset[str]` for strictly $\mathcal{O}(1)$ membership checks.
  - Implemented `__call__(self, current_user: Annotated[UserEntity, Depends(get_current_user)]) -> UserEntity` returning the verified entity or raising `HTTPException(403, detail="Insufficient role permissions")`.
  - Re-factored `get_current_active_admin = RoleChecker([UserRole.ADMIN], detail="Administrative privileges required")`.
- **Hierarchical Dependency Chaining & IDOR Protection (`app/core/dependencies.py`)**:
  - Implemented `require_user_ownership` chaining `user_id: Annotated[int, Path(..., ge=1, le=2_147_483_647)]` and `current_user: Annotated[UserEntity, Depends(get_current_user)]`.
  - Enforced ownership invariance: requests proceed ONLY if `current_user.id == user_id` OR `current_user.role == UserRole.ADMIN`.
  - Prevents Insecure Direct Object Reference (IDOR) attacks on profile mutations (`PUT /users/{user_id}`, `PATCH /users/{user_id}`).
- **Sub-Dependency Request-Scoped Caching (`use_cache=True`)**:
  - Validated FastAPI's DAG resolution: when an endpoint depends on multiple sub-dependencies that each depend on `get_current_user`, FastAPI executes `get_current_user` **strictly once** per HTTP request, caching the result in the request scope.
  - Verified via isolated test spying with `pytest.MonkeyPatch`.
- **Endpoint Integration & Route Precedence (`app/routers/user_router.py`)**:
  - Applied `require_user_ownership` to `PUT /users/{user_id}` and `PATCH /users/{user_id}`.
  - Added `GET /users/admin/metrics` guarded by `RoleChecker([UserRole.ADMIN])`, declared **before** `GET /users/{user_id}` to avoid routing collisions.
- **Dedicated Test Suite & TestClient Verification (`tests/test_dependency_chaining.py`)**:
  - Tested `RoleChecker`: standard users rejected with 403; enterprise/admin authorized with 200.
  - Tested IDOR Protection: User A updating User A succeeds (200); User A updating User B rejected (403); Admin updating User B succeeds (200).
  - Tested Request-Scoped Memoization: verified single execution of user resolution per request DAG.

---

## 2. Architectural Decisions Made
- **Pure Dependency Inversion for Authorization**: Router handler functions contain ZERO imperative authorization checks (`if user.role ...`). Security boundaries are enforced 100% declaratively via FastAPI `Depends()`.
- **Callable Class Reusability**: Using `RoleChecker` allows arbitrary combinations of roles (e.g. `RoleChecker([UserRole.ADMIN, UserRole.ENTERPRISE])`) without copy-pasting role logic or creating explosion of one-off guard functions.
- **DAG Caching vs Override Traps**: Solved the recursive dependency override hazard by utilizing `monkeypatch` spying at the service boundary rather than circular self-referential `app.dependency_overrides`.

---

## 3. DSA Time & Space Complexity Enforced
- **Role Verification**: $\mathcal{O}(1)$ time complexity using `frozenset[str]` membership lookups.
- **Ownership Verification**: $\mathcal{O}(1)$ integer equality check (`current_user.id == user_id`).
- **Request-Scoped DAG Execution**: $\mathcal{O}(1)$ sub-dependency retrieval from FastAPI's request-level dependency cache.

---

## 4. Summary of Test Results & Quality Gates
- **Pytest**: 217 passed in 1.04s (`100%` pass rate across 24 test modules).
  - `tests/test_dependency_chaining.py`: 8 new security & DAG tests passing.
  - `tests/test_auth_dependencies.py`: 12 security & dependency tests passing.
  - `tests/test_pytest_architecture.py`: 60 parametrized tests passing.
  - `tests/test_path_query_validation.py`: 40 tests passing.
  - `tests/test_field_validators.py`: 23 tests passing.
  - `tests/test_model_validators.py`: 9 tests passing.
  - `tests/test_schemas.py`: 25 tests passing.
  - `tests/test_user.py`: 28 tests passing.
  - `tests/test_user_crud.py`: 5 tests passing.
  - `tests/test_user_repository.py`: 7 tests passing.
- **Mypy**: `Success: no issues found in 26 source files` (`mypy --strict app tests`).
- **Ruff**: `All checks passed!` across `app/` and `tests/`.

---

## 5. Suggested Git Commit
```bash
git commit -m "feat(day-09): implement parameterized callable class guards, IDOR ownership dependencies, and DAG memoization"
```
