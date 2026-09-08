# RCA: Day 09 Dependency Override Self-Referential Recursion and IDOR Ownership Hazards

- **Trigger**: `RecursionError: maximum recursion depth exceeded` during sub-dependency caching test execution and IDOR vulnerability analysis.

---

## 1. Incident 1: Infinite Recursion via Self-Referential Dependency Override

### Faulty Code / Pattern
```python
# tests/test_dependency_chaining.py
def spy_get_current_user(
    real_user: UserEntity = Depends(get_current_user),
) -> UserEntity:
    call_count += 1
    return real_user

# Attempting to wrap get_current_user by overriding itself:
app.dependency_overrides[get_current_user] = spy_get_current_user
```

### Root Cause
When FastAPI resolves dependencies for a request, it checks `app.dependency_overrides` for each dependency function. When `Depends(get_current_user)` is encountered, FastAPI substitutes it with `spy_get_current_user`. However, `spy_get_current_user` itself declares a dependency `Depends(get_current_user)`. When FastAPI recursively inspects `spy_get_current_user`'s signature, it checks `app.dependency_overrides[get_current_user]` again, finding `spy_get_current_user`. This creates an infinite cycle in the dependency graph resolution engine:
```text
solve_dependencies(spy_get_current_user)
  -> solve_dependencies(get_current_user) -> override: spy_get_current_user
    -> solve_dependencies(spy_get_current_user)
      ...
RecursionError: maximum recursion depth exceeded
```

### Resolution
Never self-reference the overridden dependency in `app.dependency_overrides`. To spy on or test request-scoped execution counts of an internal domain dependency, monitor an underlying service or repository method (e.g. using `monkeypatch.setattr(UserService, "get_user_by_username", spy)`) or override with a clean, standalone provider that does not circularly depend on the target:
```python
# tests/test_dependency_chaining.py
original_get_by_username = UserService.get_user_by_username

def spy_get_by_username(svc_self: UserService, username: str) -> UserEntity:
    nonlocal call_count
    call_count += 1
    return original_get_by_username(svc_self, username=username)

monkeypatch.setattr(UserService, "get_user_by_username", spy_get_by_username)
```

---

## 2. Incident 2: Insecure Direct Object Reference (IDOR) on Profile Mutations

### Faulty Code / Pattern
```python
# app/routers/user_router.py
@router.patch("/{user_id}", response_model=UserResponse)
def update_user_profile(
    payload: UserProfileUpdate,
    user_id: int = Path(..., ge=1),
    service: Annotated[UserService, Depends(get_user_service)] = None,
) -> UserResponse:
    # Any authenticated (or unauthenticated) caller could alter ANY user's profile!
    return service.update_profile(user_id=user_id, payload=payload)
```

### Root Cause
The endpoint accepted `user_id` directly from the URL path parameter without asserting that the requester is either the owner of that resource (`current_user.id == user_id`) or an administrator with elevated privileges. This exposed a critical IDOR vulnerability where an attacker could mutate another user's email, name, or role simply by iterating integer IDs.

### Resolution
Introduced the declarative chained dependency `require_user_ownership` which resolves both `user_id` and `current_user`:
```python
# app/core/dependencies.py
def require_user_ownership(
    user_id: Annotated[int, Path(..., ge=1, le=2_147_483_647)],
    current_user: Annotated[UserEntity, Depends(get_current_user)],
) -> UserEntity:
    user_role_str = (
        current_user.role.value
        if isinstance(current_user.role, UserRole)
        else str(current_user.role)
    )
    is_owner = current_user.id == user_id
    is_admin = user_role_str == UserRole.ADMIN.value

    if not (is_owner or is_admin):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access forbidden: you cannot modify another user's profile",
        )
    return current_user
```

---

## Permanent Prevention Rules
1. **Never Self-Reference in `app.dependency_overrides`**: Dependency overrides must resolve cleanly without declaring dependencies on the key being overridden.
2. **Declarative Ownership Guarding**: All mutating endpoints operating on user-owned resources (`PUT /users/{id}`, `PATCH /users/{id}`) must be guarded by `require_user_ownership` or an equivalent ownership sub-dependency.
3. **$\mathcal{O}(1)$ Role Sets via `frozenset` in Callable Guards**: Parameterized class guards like `RoleChecker` must store allowed roles in immutable `frozenset` collections for constant-time membership validation.
