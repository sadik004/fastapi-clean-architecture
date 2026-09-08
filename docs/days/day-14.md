# Day 14: Global Exception Handling (Domain Exception Hierarchy, Centralized Error Envelope & Safe 500 Masking)

**Date**: 2026-09-08  
**Role**: Junior Apprentice Backend Engineer  
**Lead Architect & Mentor**: User

---

## 1. Concepts Covered Today
- **Centralized Error Envelope Contract (`app/schemas/error.py`)**:
  - Implemented a unified, standardized enterprise error contract across all HTTP error conditions (400, 401, 403, 404, 409, 422, 500):
    - `code`: Machine-readable, uppercase error classification (e.g. `ENTITY_NOT_FOUND`, `ENTITY_CONFLICT`, `FIELD_VALIDATION_ERROR`, `INTERNAL_SERVER_ERROR`).
    - `message`: Human-readable error description.
    - `status_code`: Corresponding HTTP status code.
    - `timestamp`: UTC ISO datetime when the error occurred.
    - `trace_id`: Unique UUIDv4 correlation trace identifier for support and observability triage.
    - `details`: Structured array of contextual field-level issues (for Pydantic validation failures) or metadata.
- **Decoupled Domain Exception Hierarchy (`app/core/exceptions.py`)**:
  - Completely decoupled domain services and repositories from the HTTP transport layer.
  - Established a clean domain exception hierarchy:
    - `BaseDomainException(Exception)`
      - `EntityNotFoundException(BaseDomainException)` (HTTP 404)
      - `EntityConflictException(BaseDomainException)` (HTTP 409)
      - `AuthorizationException(BaseDomainException)` (HTTP 403)
      - `BusinessRuleViolationException(BaseDomainException)` (HTTP 400)
    - `UserNotFoundException` inherits from `EntityNotFoundException`.
    - `UserAlreadyExistsException` inherits from `EntityConflictException`.
  - Services and Repositories never import or raise `HTTPException`.
- **Global Centralized Exception Handlers (`app/core/exception_handlers.py`)**:
  - Registered four dedicated handlers on the FastAPI application:
    1. `DomainExceptionHandler`: Intercepts domain exceptions and maps them to unified `ErrorResponse` with appropriate HTTP status codes in $\mathcal{O}(1)$ time.
    2. `ValidationExceptionHandler`: Intercepts `RequestValidationError` (422), parses field locations and validation messages, and formats them into structured `details`.
    3. `HTTPExceptionHandler`: Normalizes standard `StarletteHTTPException` / `fastapi.HTTPException` (e.g. 401, 403) into the error envelope while preserving custom security headers (e.g. `WWW-Authenticate: ApiKey`).
    4. `UnhandledExceptionCatchAllHandler`: Catches unexpected unhandled `Exception` (500), logs full stack trace securely to internal monitoring with a unique `trace_id`, and returns a safe, sanitized response:
       `{"error": {"code": "INTERNAL_SERVER_ERROR", "message": "An unexpected error occurred. Please contact support with trace ID.", "status_code": 500, "trace_id": "<uuid>"}}`.
- **Zero Information Leakage Policy**:
  - Enforced strict protection against leaking internal Python tracebacks, database credentials, SQL error fragments, or local file paths in production 500 responses.

---

## 2. Architectural Decisions Made
- **Clean Architecture Decoupling & Thin Routers**:
  - Removed all boilerplate `try...except UserNotFoundException / UserAlreadyExistsException: raise HTTPException(...)` blocks from router handlers in `app/routers/user_router.py`.
  - Routers now cleanly execute service coroutines and return validated response DTOs. When business rules or entity lookups fail, domain exceptions bubble naturally to the application-level `DomainExceptionHandler`.
- **Header Preservation**:
  - `HTTPExceptionHandler` inspects `getattr(exc, "headers", None)` and forwards any headers (such as `WWW-Authenticate`) to the final `JSONResponse`, ensuring standards compliance (RFC 7235).
- **Backward-Compatible Dual Envelope**:
  - `ErrorResponse` provides `error: ErrorDetail` as the primary contract while retaining an optional root `detail: Optional[Any] = None` attribute, allowing legacy API consumers and existing test suites to transition smoothly without breaking changes.

---

## 3. DSA Time & Space Complexity Enforced
- **Exception Resolution & Mapping**:
  - Time Complexity: Strictly $\mathcal{O}(1)$ resolution using dictionary lookup (`_HTTP_STATUS_CODE_MAP`) and `isinstance` checks over a small, fixed inheritance tree.
  - Space Complexity: Strictly bounded $\mathcal{O}(1)$ space per error event.
- **Validation Detail Parsing**:
  - Time Complexity: Linear with respect to the number of validation issues $\mathcal{O}(K)$ where $K \le 10$ in normal requests.
  - Space Complexity: Strictly $\mathcal{O}(K)$ intermediate dictionary allocation.

---

## 4. Summary of Test Results & Quality Gates
- **Pytest**: 249 passed in 12.01s (`100%` pass rate across 29 test modules).
  - `tests/test_global_exception_handling.py`: 7 tests passing:
    1. `test_404_entity_not_found_unified_envelope`: Validates 404 response structure, code, trace_id, and message.
    2. `test_404_by_username_unified_envelope`: Validates username not found mapping.
    3. `test_409_entity_conflict_unified_envelope`: Validates conflict mapping on duplicate registration.
    4. `test_422_field_validation_unified_envelope`: Validates structured field error pointers in `details`.
    5. `test_500_unhandled_exception_masked_and_trace_id`: Validates masked 500 error and zero information leakage.
    6. `test_401_and_403_security_exceptions_preserve_headers`: Validates 401/403 mapping and `WWW-Authenticate` header preservation.
    7. `test_domain_exception_hierarchy_invariants`: Verifies class inheritance and default code constants.
  - All 242 previous tests continue to pass with zero regressions.
- **Mypy**: `Success: no issues found in 35 source files` (`mypy --strict app tests`).
- **Ruff**: `All checks passed!` across `app/` and `tests/`.
