# RCA: Day 01 3-Tier Layer Coupling & In-Memory Linear Scan Trap

- **Date**: 2026-09-08
- **Trigger**: Architectural boundary audit during initial project initialization.

---

## 1. Incident 1: HTTP Transport Coupling Inside Business Logic

### Faulty Code / Pattern
```python
# app/services/user_service.py
from fastapi import HTTPException, status

class UserService:
    def register_user(self, payload: UserCreate):
        if self._repo.get_by_email(payload.email):
            # Anti-pattern: Service layer throwing HTTP transport exceptions
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Email already registered",
            )
```

### Root Cause
Directly importing and raising `fastapi.HTTPException` in the service layer couples core business logic to the HTTP transport layer. If the service is later invoked by background workers, CLI commands, gRPC handlers, or WebSocket consumers, HTTP concepts leak inappropriately into non-HTTP runtimes.

### Resolution
Decoupled domain exceptions from HTTP transport. Services throw pure domain exceptions; routers catch them and map to HTTP status codes:
```python
# app/core/exceptions.py
class DomainException(Exception):
    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(self.message)

class UserAlreadyExistsException(DomainException):
    pass

# app/services/user_service.py
if self._repo.get_by_email(payload.email):
    raise UserAlreadyExistsException("A user with this email or username already exists.")

# app/routers/user_router.py
try:
    return service.register_user(payload)
except UserAlreadyExistsException as exc:
    raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=exc.message) from exc
```

---

## 2. Incident 2: $\mathcal{O}(n)$ Linear Scans Over Storage for Uniqueness Checks

### Faulty Code / Pattern
```python
# app/repositories/user_repository.py
class InMemoryUserRepository:
    def __init__(self) -> None:
        self._users: list[UserEntity] = []

    def get_by_email(self, email: str) -> Optional[UserEntity]:
        # Anti-pattern: O(n) linear search over list
        for user in self._users:
            if user.email == email:
                return user
        return None
```

### Root Cause
Storing records in a flat list or scanning dictionary values sequentially degrades lookup performance to linear time $\mathcal{O}(n)$. At scale or under high concurrent load, uniqueness verification becomes a primary bottleneck.

### Resolution
Maintained secondary inverted hash indexes alongside primary storage to guarantee strictly $\mathcal{O}(1)$ uniqueness checks and lookups:
```python
# app/repositories/user_repository.py
class InMemoryUserRepository:
    def __init__(self) -> None:
        self._store: dict[int, UserEntity] = {}
        self._email_index: dict[str, int] = {}
        self._username_index: dict[str, int] = {}

    def get_by_email(self, email: str) -> Optional[UserEntity]:
        user_id = self._email_index.get(email)
        return self._store.get(user_id) if user_id is not None else None
```

---

## Permanent Prevention Rules Added to SKILL.md
1. **Domain-Driven Exception Isolation**: Services must never import or raise `fastapi.HTTPException`. Only routers translate domain exceptions to HTTP status codes.
2. **Strict $\mathcal{O}(1)$ Uniqueness Indexing**: In-memory repositories must maintain inverted hash maps for all unique fields. Linear scans over lists or dictionaries are strictly forbidden.
