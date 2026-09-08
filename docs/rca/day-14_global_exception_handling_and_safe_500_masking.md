# RCA: Day 14 Global Exception Handling, Domain Hierarchy & Safe 500 Masking

- **Trigger**: Inconsistent error response shapes across endpoints, information leakage risks in unhandled 500 exceptions, and coupling domain services to `HTTPException`.

---

## 1. Incident 1: Information Leakage & Stack Trace Exposure in Unhandled 500 Errors

### Faulty Code / Pattern
```python
# Default FastAPI / Starlette unhandled exception behavior
@app.get("/crash")
async def crash_endpoint():
    raise DatabaseConnectionError("Failed connecting to postgres://admin:SuperSecretPassword@10.0.0.5:5432/db")
# Returns: Raw Python traceback, local file paths, internal SQL statements, and database credentials!
```

### Root Cause
When an unexpected exception occurs in an unhandled route or service, default application frameworks may either dump the raw Python traceback or expose internal error messages directly in the HTTP 500 response body. This creates a severe security vulnerability (CWE-209: Generation of Error Message Containing Sensitive Information), exposing database connection strings, credentials, and internal filesystem layouts to potential attackers.

### Resolution
Implement an application-level catch-all exception handler (`unhandled_exception_handler`) that intercepts all unexpected exceptions. Securely log the full traceback internally with a unique UUID `trace_id`, but return a sanitized, masked response to the client:
```python
# app/core/exception_handlers.py
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    trace_id = str(uuid.uuid4())
    logger.error(
        "CRITICAL: Unhandled internal exception [trace_id=%s] at '%s': %s",
        trace_id,
        request.url.path,
        exc,
        exc_info=True,
    )
    # Mask client response completely
    error_detail = ErrorDetail(
        code="INTERNAL_SERVER_ERROR",
        message="An unexpected error occurred. Please contact support with trace ID.",
        status_code=500,
        timestamp=datetime.now(timezone.utc),
        trace_id=trace_id,
        details=None,
    )
    return JSONResponse(status_code=500, content=ErrorResponse(error=error_detail).model_dump(mode="json"))
```

---

## 2. Incident 2: Layering Violation (Coupling Domain Services to HTTPException)

### Faulty Code / Pattern
```python
# app/services/user_service.py
from fastapi import HTTPException

class UserService:
    async def get_user_by_id(self, user_id: int) -> UserEntity:
        user = await self._repository.get_by_id(user_id)
        if user is None:
            # VIOLATION: Raising HTTP transport exception in business logic layer!
            raise HTTPException(status_code=404, detail=f"User {user_id} not found")
        return user
```

### Root Cause
Raising `HTTPException` directly inside the Service or Repository layer tightly couples the domain and business logic to the HTTP transport layer. This violates 3-Tier Clean Architecture, preventing the domain services from being reused across non-HTTP interfaces (such as asynchronous Celery/Inngest workers, CLI commands, or gRPC/RPC protocols) without importing web frameworks.

### Resolution
Establish a pure, decoupled domain exception hierarchy (`BaseDomainException`, `EntityNotFoundException`, `EntityConflictException`, `AuthorizationException`, `BusinessRuleViolationException`). Domain services raise only domain exceptions, and global exception handlers translate them into HTTP responses:
```python
# app/core/exceptions.py
class BaseDomainException(Exception):
    def __init__(self, message: str, code: str = "DOMAIN_ERROR") -> None:
        self.message = message
        self.code = code
        super().__init__(self.message)

class EntityNotFoundException(BaseDomainException):
    def __init__(self, message: str = "Requested entity was not found.", code: str = "ENTITY_NOT_FOUND") -> None:
        super().__init__(message=message, code=code)

# app/services/user_service.py
if user is None:
    raise UserNotFoundException(user_id=user_id)
```

---

## 3. Incident 3: Validation Error Structure & Backward Compatibility

### Faulty Code / Pattern
```python
# tests/test_user.py
response = client.post("/users/", json=invalid_payload)
assert response.status_code == 422
errors = response.json().get("detail", [])
assert any(err.get("loc")[-1] == field for err in errors)
# If ErrorResponse sets detail: str = "Request validation failed...",
# errors is a string! err.get("loc") raises AttributeError: 'str' object has no attribute 'get'!
```

### Root Cause
FastAPI's default 422 handler returns `{ "detail": [ { "loc": [...], "msg": "...", "type": "..." } ] }`. When migrating to an enterprise error envelope `{ "error": { "code": "FIELD_VALIDATION_ERROR", ... } }`, legacy client applications and existing test suites expecting `response.json()["detail"]` as a list of error dicts fail with `AttributeError`.

### Resolution
Design `ErrorResponse` with a flexible dual-envelope structure: `error: ErrorDetail` as the canonical contract, and `detail: Optional[Any] = None` populated with the raw validation list for 422 errors and string messages for 4xx/5xx errors, ensuring 100% backward compatibility with zero breaking changes for existing consumers.

---

## 4. Preventive Rules Codified
1. **Decoupled Domain Exceptions**: Never raise `HTTPException` in domain services or repositories; raise `BaseDomainException` subclasses.
2. **Zero Information Leakage**: Mask unhandled 500 errors in production and correlate with UUID `trace_id`.
3. **Preserve HTTP Headers**: Always preserve response headers (e.g. `WWW-Authenticate`) when mapping HTTP exceptions in global handlers.
4. **Dual Envelope for Seamless Migration**: Provide backward-compatible root attributes during major error schema migrations.
