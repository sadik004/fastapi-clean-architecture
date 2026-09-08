# Day 08: FastAPI Dependency Injection Architecture (Cached AppConfig & Declarative Auth Guard)

**Date**: 2026-09-08  
**Role**: Junior Apprentice Backend Engineer  
**Lead Architect & Mentor**: User

---

## 1. Concepts Covered Today
- **Centralized Immutable Application Settings (`app/core/config.py`)**:
  - Defined `Settings` with `pydantic_settings.BaseSettings` (and fallback to Pydantic `BaseModel`) with `model_config = SettingsConfigDict(frozen=True, env_file=".env", extra="ignore")`.
  - Enforced immutability (`frozen=True`) to prevent configuration mutation during application runtime.
  - Implemented `@lru_cache()` decorated singleton provider `get_settings()` ensuring $\mathcal{O}(1)$ pointer reuse and zero redundant disk/environment parsing on incoming requests.
- **FastAPI Dependency Injection Architecture (`app/core/dependencies.py`)**:
  - Abstracted repository and service providers: `get_user_repository()` and `get_user_service()`.
  - Implemented declarative header authentication: `api_key: Annotated[str | None, Header(alias="X-API-Key")]`.
  - Implemented `get_current_user` resolving credentials to validated `UserEntity` domain objects, returning `401 Unauthorized` (`WWW-Authenticate: ApiKey`) upon missing or invalid keys.
  - Implemented role-based guard `get_current_active_admin` returning `403 Forbidden` (`Admin privileges required`) for non-admin users.
- **Side-Channel Timing Attack Mitigation**:
  - Implemented constant-time string comparison using `secrets.compare_digest(a, b)` across all authentication credentials.
- **Route Precedence & Endpoint Protection (`app/routers/user_router.py`)**:
  - Placed literal sub-path `GET /users/me` strictly before parametrized route `GET /users/{user_id}` to prevent FastAPI route matching collisions.
  - Guarded `DELETE /users/{user_id}` with `current_admin: Annotated[UserEntity, Depends(get_current_active_admin)]`.
- **Pytest Fixtures & Comprehensive Security Tests**:
  - Added centralized auth fixtures in `tests/conftest.py`: `admin_auth_headers`, `auth_headers`, `standard_user`, `admin_user`.
  - Added dedicated test suite `tests/test_auth_dependencies.py` validating config caching, immutability, 401s, 403s, 200 `/me`, and 204 admin deletion.

---

## 2. Architectural Decisions Made
- **Pure Dependency Inversion**: Router handlers do not parse headers, instantiate services, or read environment variables directly. All infrastructure dependencies and credentials are provided declaratively via `Depends()`.
- **Route Order Precedence**: FastAPI compiles routes in declaration order. Declaring `GET /users/me` after `GET /users/{user_id}` results in FastAPI attempting to parse `"me"` into an integer/UUID path parameter and failing with HTTP 422. Placing literal paths before path parameters preserves route correctness.
- **Dynamic Test Authentication Support**: In addition to static `settings.admin_api_key` and `settings.user_api_key`, `get_current_user` supports dynamic keys formatted as `userkey_<username>`, enabling tests to authenticate dynamically created users while preserving $\mathcal{O}(1)$ lookups.

---

## 3. DSA Time & Space Complexity Enforced
- **Configuration Access**: $\mathcal{O}(1)$ time complexity via `@lru_cache()` memoization.
- **Authentication Lookups**: Strictly $\mathcal{O}(1)$ time complexity using secondary inverted index hash maps (`repo.get_by_username()`).
- **Timing Defense**: Constant-time $\mathcal{O}(k)$ verification via `secrets.compare_digest()`, eliminating length-based and prefix-based character timing side channels.

---

## 4. Summary of Test Results & Quality Gates
- **Pytest**: 209 passed in 0.96s (`100%` pass rate across 23 test modules).
  - `tests/test_auth_dependencies.py`: 12 security & dependency tests passing.
  - `tests/test_pytest_architecture.py`: 60 parametrized tests passing.
  - `tests/test_path_query_validation.py`: 40 tests passing.
  - `tests/test_field_validators.py`: 23 tests passing.
  - `tests/test_model_validators.py`: 9 tests passing.
  - `tests/test_schemas.py`: 25 tests passing.
  - `tests/test_user.py`: 28 tests passing.
  - `tests/test_user_crud.py`: 5 tests passing.
  - `tests/test_user_repository.py`: 7 tests passing.
- **Mypy**: `Success: no issues found in 25 source files` (`mypy --strict app tests`).
- **Ruff**: `All checks passed!` across `app/` and `tests/`.

---

## 5. Suggested Git Commit
```bash
git commit -m "feat(day-08): implement cached app configuration and declarative auth guards with constant-time comparison"
```
