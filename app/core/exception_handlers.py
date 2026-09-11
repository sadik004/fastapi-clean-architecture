"""Centralized Global Exception Handlers & Standardized Error Envelope Dispatchers.

This module intercepts domain exceptions, Pydantic validation errors, HTTP exceptions,
and unhandled 500 crashes, translating them into the standardized enterprise ErrorResponse contract.

Key Guarantees:
1. Zero Stack Trace Leaks: Unhandled 500 exceptions are masked; internal tracebacks
   are logged securely with a unique UUID trace_id.
2. Preserved Headers: Security response headers (e.g. WWW-Authenticate) are retained.
3. O(1) Parsing Complexity: Predictable constant-time serialization into the error envelope.
"""

import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.exceptions import (
    AuthenticationException,
    AuthorizationException,
    BaseDomainException,
    BusinessRuleViolationException,
    ConnectionPoolExhaustedException,
    DatabaseQueryTimeoutException,
    EntityConflictException,
    EntityNotFoundException,
    SecurityViolationException,
    ServiceUnavailableException,
    UnbalancedJournalEntryException,
    ValidationException,
)
from app.schemas.error import ErrorDetail, ErrorResponse

logger = logging.getLogger("app.exception_handlers")

# HTTP status code to standardized machine-readable error code mapping
_HTTP_STATUS_CODE_MAP: dict[int, str] = {
    400: "BAD_REQUEST",
    401: "UNAUTHORIZED",
    403: "FORBIDDEN",
    404: "NOT_FOUND",
    405: "METHOD_NOT_ALLOWED",
    409: "CONFLICT",
    422: "UNPROCESSABLE_ENTITY",
    429: "RATE_LIMITED",
    503: "SERVICE_UNAVAILABLE",
    504: "GATEWAY_TIMEOUT",
}


def _resolve_domain_status_code(exc: BaseDomainException) -> int:
    """Map domain exception hierarchy to appropriate HTTP status codes in O(1) time."""
    if isinstance(exc, AuthenticationException):
        return 401
    if isinstance(exc, EntityNotFoundException):
        return 404
    if isinstance(exc, EntityConflictException):
        return 409
    if isinstance(exc, AuthorizationException):
        return 403
    if isinstance(exc, UnbalancedJournalEntryException):
        return 422
    if isinstance(exc, SecurityViolationException | BusinessRuleViolationException | ValidationException):
        return 400
    if isinstance(exc, DatabaseQueryTimeoutException):
        return 504
    if isinstance(exc, ConnectionPoolExhaustedException | ServiceUnavailableException):
        return 503
    return 400


async def domain_exception_handler(
    request: Request,
    exc: BaseDomainException,
) -> JSONResponse:
    """Translate decoupled domain exceptions into standardized ErrorResponse envelopes."""
    status_code = _resolve_domain_status_code(exc)
    trace_id = getattr(request.state, "request_id", None) or str(uuid.uuid4())

    error_detail = ErrorDetail(
        code=exc.code,
        message=exc.message,
        status_code=status_code,
        timestamp=datetime.now(UTC),
        trace_id=trace_id,
        details=None,
    )
    error_response = ErrorResponse(
        error=error_detail,
        detail=exc.message,
    )

    logger.warning(
        "Domain exception intercepted [%s] at '%s': %s (trace_id=%s)",
        exc.code,
        request.url.path,
        exc.message,
        trace_id,
    )

    headers: dict[str, str] = {}
    exc_headers = getattr(exc, "headers", None)
    if isinstance(exc_headers, dict):
        headers.update(exc_headers)
    retry_after_val = getattr(exc, "retry_after", None)
    if retry_after_val is not None:
        headers["Retry-After"] = str(retry_after_val)

    return JSONResponse(
        status_code=status_code,
        content=error_response.model_dump(mode="json"),
        headers=headers if headers else None,
    )


async def validation_exception_handler(
    request: Request,
    exc: RequestValidationError,
) -> JSONResponse:
    """Standardize Pydantic / FastAPI request validation failures into unified details."""
    status_code = 422
    trace_id = getattr(request.state, "request_id", None) or str(uuid.uuid4())

    parsed_details: list[dict[str, Any]] = [
        {
            "field": ".".join(str(loc) for loc in err.get("loc", []) if loc != "body"),
            "message": err.get("msg", ""),
            "type": err.get("type", ""),
            "location": [str(x) for x in err.get("loc", [])],
        }
        for err in exc.errors()
    ]

    message = "Request validation failed. Check 'details' for field-level errors."
    error_detail = ErrorDetail(
        code="FIELD_VALIDATION_ERROR",
        message=message,
        status_code=status_code,
        timestamp=datetime.now(UTC),
        trace_id=trace_id,
        details=parsed_details,
    )
    raw_errors = [
        {
            "loc": list(err.get("loc", [])),
            "msg": str(err.get("msg", "")),
            "type": str(err.get("type", "")),
        }
        for err in exc.errors()
    ]
    error_response = ErrorResponse(
        error=error_detail,
        detail=raw_errors,
    )

    logger.info(
        "Validation error at '%s' (trace_id=%s): %d issue(s)",
        request.url.path,
        trace_id,
        len(parsed_details),
    )

    return JSONResponse(
        status_code=status_code,
        content=error_response.model_dump(mode="json"),
    )


async def http_exception_handler(
    request: Request,
    exc: StarletteHTTPException,
) -> JSONResponse:
    """Normalize Starlette / FastAPI HTTPExceptions while preserving response headers."""
    status_code = exc.status_code
    trace_id = getattr(request.state, "request_id", None) or str(uuid.uuid4())
    code = _HTTP_STATUS_CODE_MAP.get(status_code, f"HTTP_{status_code}")
    message = str(exc.detail) if exc.detail else "An HTTP error occurred."

    error_detail = ErrorDetail(
        code=code,
        message=message,
        status_code=status_code,
        timestamp=datetime.now(UTC),
        trace_id=trace_id,
        details=None,
    )
    error_response = ErrorResponse(
        error=error_detail,
        detail=message,
    )

    logger.warning(
        "HTTP exception [%d %s] at '%s': %s (trace_id=%s)",
        status_code,
        code,
        request.url.path,
        message,
        trace_id,
    )

    headers = getattr(exc, "headers", None)
    return JSONResponse(
        status_code=status_code,
        content=error_response.model_dump(mode="json"),
        headers=headers,
    )


async def unhandled_exception_handler(
    request: Request,
    exc: Exception,
) -> JSONResponse:
    """Mask unexpected 500 server crashes to prevent leaking tracebacks or internals."""
    trace_id = getattr(request.state, "request_id", None) or str(uuid.uuid4())
    status_code = 500

    # Log full traceback and internal error details securely to internal monitoring
    logger.error(
        "CRITICAL: Unhandled internal exception [trace_id=%s] at '%s': %s",
        trace_id,
        request.url.path,
        exc,
        exc_info=True,
    )

    # Sanitize and mask the client-facing response (Zero Information Leakage)
    masked_message = "An unexpected error occurred. Please contact support with trace ID."
    error_detail = ErrorDetail(
        code="INTERNAL_SERVER_ERROR",
        message=masked_message,
        status_code=status_code,
        timestamp=datetime.now(UTC),
        trace_id=trace_id,
        details=None,
    )
    error_response = ErrorResponse(
        error=error_detail,
        detail=masked_message,
    )

    return JSONResponse(
        status_code=status_code,
        content=error_response.model_dump(mode="json"),
    )


def register_exception_handlers(app: FastAPI) -> None:
    """Register all centralized exception handlers onto the FastAPI application instance."""
    app.add_exception_handler(BaseDomainException, domain_exception_handler)  # type: ignore[arg-type]
    app.add_exception_handler(RequestValidationError, validation_exception_handler)  # type: ignore[arg-type]
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)  # type: ignore[arg-type]
    app.add_exception_handler(Exception, unhandled_exception_handler)
