"""Enterprise Structured JSON Logging Architecture with Structlog & PII Redaction.

Provides high-velocity, machine-readable JSON telemetry with UTC ISO 8601 timestamps,
dynamic contextvars correlation tracking, structured exception traces, and O(1) PII scrubbing.
"""

from __future__ import annotations

import logging
import sys
from typing import Any

import structlog
from structlog.types import EventDict, Processor

# Pre-compiled frozenset for O(1) sensitive key identification
SENSITIVE_KEY_NAMES: frozenset[str] = frozenset({
    "password",
    "token",
    "secret",
    "authorization",
    "credit_card",
    "access_token",
    "refresh_token",
    "api_key",
    "client_secret",
    "private_key",
    "auth_token",
    "user_password",
})

SENSITIVE_SUFFIXES: tuple[str, ...] = (
    "_password",
    "_secret",
    "_token",
    "_key",
    "_credit_card",
)

REDACTED_STR: str = "[REDACTED]"


def is_sensitive_key(key: str) -> bool:
    """Determine if a dictionary key represents sensitive or PII data in O(1) time.

    Args:
        key: The key name to check.

    Returns:
        True if the key is deemed sensitive, False otherwise.
    """
    key_clean = key.lower().replace("-", "_")
    if key_clean in SENSITIVE_KEY_NAMES:
        return True
    return key_clean.endswith(SENSITIVE_SUFFIXES)


def scrub_data(val: Any) -> Any:
    """Recursively scrub sensitive fields from nested dictionaries and collections.

    Args:
        val: Value to inspect and scrub.

    Returns:
        Scrubbed data structure with sensitive fields replaced by [REDACTED].
    """
    if isinstance(val, dict):
        scrubbed: dict[str, Any] = {}
        for k, v in val.items():
            if isinstance(k, str) and is_sensitive_key(k):
                scrubbed[k] = REDACTED_STR
            else:
                scrubbed[k] = scrub_data(v)
        return scrubbed
    if isinstance(val, list):
        return [scrub_data(item) for item in val]
    return val


def redact_sensitive_data_processor(
    logger: Any,
    method_name: str,
    event_dict: EventDict,
) -> EventDict:
    """Structlog processor that redacts sensitive PII fields in O(1) lookup time.

    Scrubs passwords, authentication tokens, secrets, credentials, and credit card numbers.
    """
    for key in list(event_dict.keys()):
        if is_sensitive_key(str(key)):
            event_dict[key] = REDACTED_STR
        else:
            val = event_dict[key]
            if isinstance(val, dict | list):
                event_dict[key] = scrub_data(val)
    return event_dict


def get_processors(
    environment: str = "production",
    force_json: bool = False,
) -> tuple[list[Processor], Processor]:
    """Build the structlog shared processor chain and output renderer.

    Args:
        environment: Current application runtime environment ("production" or "development").
        force_json: If True, mandates JSONRenderer regardless of environment.

    Returns:
        A tuple of (shared_processors, output_renderer).
    """
    shared_processors: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        redact_sensitive_data_processor,
    ]

    if environment.lower() == "production" or force_json:
        renderer: Processor = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer(colors=True)

    return shared_processors, renderer


def setup_logging(
    environment: str = "production",
    log_level: int = logging.INFO,
    force_json: bool = False,
) -> None:
    """Configure enterprise structlog pipeline and intercept standard library logging.

    Args:
        environment: Current environment (e.g. 'production', 'development', 'test').
        log_level: Standard logging level (default: logging.INFO).
        force_json: If True, forces JSON output even in development.
    """
    structlog.reset_defaults()
    shared_processors, renderer = get_processors(environment=environment, force_json=force_json)

    structlog.configure(
        processors=shared_processors + [
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=False,
    )

    # Standard library logging interceptor: redirect stdlib logging through structlog pipeline
    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared_processors,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.addHandler(handler)
    root_logger.setLevel(log_level)


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """Obtain a structured logger instance bound to a module or component name.

    Args:
        name: The component or module name (__name__).

    Returns:
        A thread-safe, coroutine-aware Structlog BoundLogger.
    """
    return structlog.get_logger(name)  # type: ignore[no-any-return]
