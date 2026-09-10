"""Custom ASGI Middleware for request latency tracking, correlation IDs, and OWASP security headers.

Ensures strict perimeter defense, RFC 9562 UUIDv7 correlation ID propagation, and microsecond-precision
structured logging across all incoming HTTP requests and outgoing responses in O(1) time complexity.
"""

from __future__ import annotations

import time

import structlog.contextvars
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from app.core.context import reset_correlation_id, set_correlation_id
from app.core.identifiers import generate_uuidv7
from app.core.logging import get_logger

logger = get_logger("app.middleware")


class CustomSecurityAndObservabilityMiddleware(BaseHTTPMiddleware):
    """Global ASGI perimeter middleware for observability and security hardening.

    Interception Pipeline:
    1. Pre-execution:
       - Captures high-precision start timestamp via time.perf_counter().
       - Resolves client-supplied 'X-Request-ID' or 'X-Correlation-ID', or creates fresh RFC 9562 UUIDv7.
       - Sets correlation ID in contextvars and binds to structlog.contextvars.
       - Binds request_id and correlation_id to request.state.
       - Logs structured 'http_request_started' event.
    2. Execution:
       - Dispatches request downstream through the ASGI application stack.
    3. Post-execution & Injection:
       - Computes duration in milliseconds (O(1) calculation).
       - Logs structured 'http_request_completed' event with latency and status code.
       - Injects observability headers: 'X-Process-Time-Ms', 'X-Request-ID', 'X-Correlation-ID'.
       - Injects OWASP defense-in-depth security headers.
    4. Teardown:
       - Clears structlog contextvars and resets contextvars token to prevent cross-task leakage.
    """

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        start_time = time.perf_counter()

        # Extract client correlation ID or generate time-ordered RFC 9562 UUIDv7
        incoming_id = request.headers.get("X-Correlation-ID") or request.headers.get("X-Request-ID")
        correlation_id = incoming_id.strip() if incoming_id and incoming_id.strip() else str(generate_uuidv7())

        # Bind to asyncio contextvars and structlog contextvars
        token = set_correlation_id(correlation_id)
        structlog.contextvars.bind_contextvars(correlation_id=correlation_id)

        request.state.request_id = correlation_id
        request.state.correlation_id = correlation_id

        client_host = request.client.host if request.client else None
        logger.info(
            "http_request_started",
            method=request.method,
            path=request.url.path,
            client_ip=client_host,
        )

        try:
            response = await call_next(request)
            duration_ms = round((time.perf_counter() - start_time) * 1000.0, 2)

            logger.info(
                "http_request_completed",
                method=request.method,
                path=request.url.path,
                status_code=response.status_code,
                latency_ms=duration_ms,
            )

            # Observability headers
            response.headers["X-Process-Time-Ms"] = f"{duration_ms:.2f}"
            response.headers["X-Request-ID"] = correlation_id
            response.headers["X-Correlation-ID"] = correlation_id

            # OWASP defense-in-depth security headers
            response.headers["X-Content-Type-Options"] = "nosniff"
            response.headers["X-Frame-Options"] = "DENY"
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
            response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
            response.headers["X-XSS-Protection"] = "0"

            return response
        except Exception as exc:
            duration_ms = round((time.perf_counter() - start_time) * 1000.0, 2)
            logger.error(
                "http_request_failed",
                method=request.method,
                path=request.url.path,
                error=str(exc),
                latency_ms=duration_ms,
                exc_info=True,
            )
            raise
        finally:
            structlog.contextvars.clear_contextvars()
            reset_correlation_id(token)
