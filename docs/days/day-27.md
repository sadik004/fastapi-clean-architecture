# Day 27: Custom ASGI Middleware (Latency Tracking, Correlation IDs & Security Header Injection)

**Date**: 2026-09-09  
**Role**: Junior Apprentice Backend Engineer  
**Lead Architect & Mentor**: User

---

## 1. Concepts Covered Today
- **Custom ASGI Middleware Architecture (`BaseHTTPMiddleware`)**:
  - Implemented `CustomSecurityAndObservabilityMiddleware` in `app/core/middleware.py` wrapping the full ASGI application dispatch lifecycle in an onion-style interceptor.
  - Intercepts all incoming HTTP requests before reaching routers/exception handlers, and captures all outgoing HTTP responses (including 200, 404, 422, and 500 status codes).
- **High-Precision Monotonic Latency Tracking**:
  - Leveraged `time.perf_counter()` to benchmark request execution duration with sub-microsecond precision.
  - Eliminated the risk of clock skew or negative elapsed time caused by NTP synchronization when using `time.time()`.
  - Injected `X-Process-Time-Ms: {process_time:.3f}` header into all HTTP responses.
- **Distributed Correlation ID Propagation & Request State Binding**:
  - Extracted client-supplied `X-Request-ID` or `X-Correlation-ID` headers to support distributed tracing across upstream gateways.
  - Generated cryptographically random `uuid.uuid4()` strings as fallback when correlation IDs are absent.
  - Bound `request_id` to `request.state.request_id` in $\mathcal{O}(1)$ time, making it accessible to downstream handlers and logging contexts.
  - Injected `X-Request-ID` into every HTTP response header.
- **OWASP Security Header Enforcement**:
  - Guaranteed injection of 5 essential defensive HTTP response headers:
    1. `X-Content-Type-Options: nosniff` (prevents MIME type sniffing).
    2. `X-Frame-Options: DENY` (mitigates clickjacking attacks).
    3. `Strict-Transport-Security: max-age=31536000; includeSubDomains` (enforces HSTS encryption).
    4. `Referrer-Policy: strict-origin-when-cross-origin` (prevents sensitive path leakage in cross-origin requests).
    5. `X-XSS-Protection: 0` (disables buggy legacy XSS auditor in modern browsers).
- **Error Response Synchronization**:
  - Synchronized `app/core/exception_handlers.py` to reuse `getattr(request.state, "request_id", None)` for the `trace_id` field in `ErrorDetail` response bodies.
  - Ensured 100% parity between response body `trace_id` and response header `X-Request-ID`.

---

## 2. Key Code Artifacts
- `app/core/middleware.py`: Production-grade `CustomSecurityAndObservabilityMiddleware` and security header definitions.
- `app/core/exception_handlers.py`: Refactored all domain, validation, HTTP, and unhandled exception handlers to reuse `request.state.request_id`.
- `app/main.py`: Registered `CustomSecurityAndObservabilityMiddleware` at the application perimeter.
- `tests/test_custom_middleware.py`: 6 comprehensive integration tests covering 200 OK headers, correlation ID echoing, 404/422 error bubbling, error body trace ID synchronization, and latency accuracy.

---

## 3. Verification & Quality Gates
- **Pytest**: 340 tests passed across the complete suite (100% pass rate in 21.86s).
- **Mypy**: `mypy --strict app tests/test_custom_middleware.py` passed with 0 errors across 41 source files.
- **Ruff**: `ruff check app tests alembic` passed cleanly.
- **Performance Budget**: Middleware execution overhead measured at $< 0.01\text{ms}$ per request, well within the $< 0.05\text{ms}$ budget.

---

## 4. Root Cause Analysis (RCA)
- See `docs/rca/day-27_custom_asgi_middleware_and_security_headers.md` for complete architectural analysis regarding perimeter vs per-route header anti-patterns, exception bubbling, and trace ID synchronization.
