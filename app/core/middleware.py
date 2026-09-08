"""Custom ASGI Middleware for request latency tracking, correlation IDs, and OWASP security headers.

Ensures strict perimeter defense and microsecond-precision observability across all
incoming HTTP requests and outgoing responses in O(1) time complexity.
"""

import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response


class CustomSecurityAndObservabilityMiddleware(BaseHTTPMiddleware):
    """Global ASGI perimeter middleware for observability and security hardening.

    Interception Pipeline:
    1. Pre-execution:
       - Captures high-precision start timestamp via time.perf_counter().
       - Resolves client-supplied 'X-Request-ID' or 'X-Correlation-ID', or creates fresh UUIDv4.
       - Binds request_id to request.state.request_id.
    2. Execution:
       - Dispatches request downstream through the ASGI application stack.
    3. Post-execution & Injection:
       - Computes duration in milliseconds (O(1) calculation).
       - Injects observability headers: 'X-Process-Time-Ms', 'X-Request-ID'.
       - Injects OWASP defense-in-depth security headers:
         * X-Content-Type-Options: nosniff
         * X-Frame-Options: DENY
         * Strict-Transport-Security: max-age=31536000; includeSubDomains
         * Referrer-Policy: strict-origin-when-cross-origin
         * X-XSS-Protection: 0
    """

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        # Pre-execution: timing and correlation ID setup
        start_time = time.perf_counter()

        incoming_id = request.headers.get("X-Request-ID") or request.headers.get("X-Correlation-ID")
        request_id = incoming_id.strip() if incoming_id and incoming_id.strip() else str(uuid.uuid4())

        request.state.request_id = request_id

        # Downstream execution
        response = await call_next(request)

        # Post-execution: duration calculation
        duration_ms = (time.perf_counter() - start_time) * 1000.0

        # Observability headers
        response.headers["X-Process-Time-Ms"] = f"{duration_ms:.2f}"
        response.headers["X-Request-ID"] = request_id

        # OWASP defense-in-depth security headers
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["X-XSS-Protection"] = "0"

        return response
