"""Custom ASGI Middleware for request latency tracking, correlation IDs, W3C Distributed Tracing, and OWASP security headers.

Ensures strict perimeter defense, OpenTelemetry W3C traceparent propagation, RFC 9562 UUIDv7 correlation ID binding,
and microsecond-precision structured logging across all incoming HTTP requests and outgoing responses in O(1) time complexity.
"""

from __future__ import annotations

import time

import structlog.contextvars
from opentelemetry.trace import SpanKind, StatusCode
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from app.core.context import reset_correlation_id, set_correlation_id
from app.core.identifiers import generate_uuidv7
from app.core.logging import get_logger
from app.core.tracing import format_span_id, format_trace_id, get_propagator, get_tracer

logger = get_logger("app.middleware")


class CustomSecurityAndObservabilityMiddleware(BaseHTTPMiddleware):
    """Global ASGI perimeter middleware for observability, distributed tracing, and security hardening.

    Interception Pipeline:
    1. Pre-execution:
       - Captures high-precision start timestamp via time.perf_counter().
       - Resolves client-supplied 'X-Request-ID' or 'X-Correlation-ID', or creates fresh RFC 9562 UUIDv7.
       - Extracts incoming W3C 'traceparent' and 'tracestate' via TraceContextTextMapPropagator.
       - Starts root SERVER span ('HTTP {method} {path}').
       - Binds correlation_id, trace_id, and span_id to structlog.contextvars for Trace-to-Log correlation.
       - Binds request_id, correlation_id, trace_id, and span_id to request.state.
       - Logs structured 'http_request_started' event.
    2. Execution:
       - Dispatches request downstream through the ASGI application stack.
    3. Post-execution & Injection:
       - Computes duration in milliseconds (O(1) calculation).
       - Logs structured 'http_request_completed' event with latency and status code.
       - Records HTTP status on root span and sets OK/ERROR span status.
       - Injects observability headers: 'X-Process-Time-Ms', 'X-Request-ID', 'X-Correlation-ID', and 'traceparent'.
       - Injects OWASP defense-in-depth security headers.
    4. Teardown:
       - Clears structlog contextvars and resets contextvars token to prevent cross-task leakage.
       - Root span context is cleanly closed.
    """

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        start_time = time.perf_counter()

        # Extract client correlation ID or generate time-ordered RFC 9562 UUIDv7
        incoming_id = request.headers.get("X-Correlation-ID") or request.headers.get("X-Request-ID")
        correlation_id = incoming_id.strip() if incoming_id and incoming_id.strip() else str(generate_uuidv7())

        # Bind correlation ID to asyncio contextvars
        token = set_correlation_id(correlation_id)

        # Extract parent trace context from incoming W3C headers (carrier dictionary)
        extracted_context = get_propagator().extract(carrier=dict(request.headers))
        tracer = get_tracer("app.middleware")

        span_name = f"HTTP {request.method} {request.url.path}"

        with tracer.start_as_current_span(
            span_name,
            context=extracted_context,
            kind=SpanKind.SERVER,
            attributes={
                "http.method": request.method,
                "http.url": str(request.url),
                "http.target": request.url.path,
                "http.scheme": request.url.scheme,
                "client.address": request.client.host if request.client else "",
            },
        ) as root_span:
            span_ctx = root_span.get_span_context()
            trace_id_hex = format_trace_id(span_ctx.trace_id) if span_ctx.is_valid else ""
            span_id_hex = format_span_id(span_ctx.span_id) if span_ctx.is_valid else ""

            # Bind to structlog contextvars for unified Trace-to-Log correlation
            structlog.contextvars.bind_contextvars(
                correlation_id=correlation_id,
                trace_id=trace_id_hex,
                span_id=span_id_hex,
            )

            request.state.request_id = correlation_id
            request.state.correlation_id = correlation_id
            request.state.trace_id = trace_id_hex
            request.state.span_id = span_id_hex

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

                # Record response telemetry on root span
                root_span.set_attribute("http.status_code", response.status_code)
                if response.status_code >= 500:
                    root_span.set_status(StatusCode.ERROR, f"HTTP status {response.status_code}")
                else:
                    root_span.set_status(StatusCode.OK)

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

                # Inject W3C traceparent header for downstream distributed callers
                if span_ctx.is_valid:
                    response.headers["traceparent"] = f"00-{trace_id_hex}-{span_id_hex}-01"

                # OWASP defense-in-depth security headers
                response.headers["X-Content-Type-Options"] = "nosniff"
                response.headers["X-Frame-Options"] = "DENY"
                response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
                response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
                response.headers["X-XSS-Protection"] = "0"

                return response
            except Exception as exc:
                duration_ms = round((time.perf_counter() - start_time) * 1000.0, 2)
                root_span.record_exception(exc)
                root_span.set_status(StatusCode.ERROR, str(exc))

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
