"""Enterprise Distributed Tracing Architecture powered by OpenTelemetry.

Provides TracerProvider management, W3C Trace Context propagation, in-memory span telemetry,
and high-precision span instrumentation decorators with automatic exception recording.
"""

from __future__ import annotations

import functools
import inspect
from collections.abc import Callable
from typing import Any, TypeVar

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode, get_tracer_provider, set_tracer_provider
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

# Service Resource Metadata
SERVICE_RESOURCE = Resource.create(
    {
        "service.name": "fastapi-clean-architecture",
        "service.version": "1.0.0",
        "deployment.environment": "production",
    }
)

# Global in-memory exporter for test isolation and diagnostics without external network dependencies
_in_memory_exporter: InMemorySpanExporter = InMemorySpanExporter()
_trace_context_propagator: TraceContextTextMapPropagator = TraceContextTextMapPropagator()

F = TypeVar("F", bound=Callable[..., Any])


def format_trace_id(trace_id: int) -> str:
    """Format an integer trace ID into a 32-character lowercase hexadecimal string."""
    return f"{trace_id:032x}"


def format_span_id(span_id: int) -> str:
    """Format an integer span ID into a 16-character lowercase hexadecimal string."""
    return f"{span_id:016x}"


def get_propagator() -> TraceContextTextMapPropagator:
    """Return the global W3C TraceContext propagator."""
    return _trace_context_propagator


def get_in_memory_exporter() -> InMemorySpanExporter:
    """Return the global in-memory span exporter instance for test assertions and diagnostics."""
    return _in_memory_exporter


def clear_in_memory_spans() -> None:
    """Clear all recorded spans from the in-memory exporter and ensure exporter is active."""
    _in_memory_exporter.clear()
    if getattr(_in_memory_exporter, "_stopped", False):
        _in_memory_exporter._stopped = False
        init_tracer()


def init_tracer(
    service_name: str = "fastapi-clean-architecture",
    environment: str = "production",
) -> TracerProvider:
    """Initialize OpenTelemetry TracerProvider and register as global tracer.

    Args:
        service_name: Name of the microservice.
        environment: Deployment environment.

    Returns:
        The configured TracerProvider.
    """
    global _in_memory_exporter
    if getattr(_in_memory_exporter, "_stopped", False):
        _in_memory_exporter._stopped = False

    resource = Resource.create(
        {
            "service.name": service_name,
            "service.version": "1.0.0",
            "deployment.environment": environment,
        }
    )

    provider = TracerProvider(resource=resource)
    provider.add_span_processor(SimpleSpanProcessor(_in_memory_exporter))
    set_tracer_provider(provider)
    return provider


def shutdown_tracer() -> None:
    """Shut down the active TracerProvider and flush remaining spans."""
    provider = get_tracer_provider()
    if isinstance(provider, TracerProvider):
        provider.shutdown()


def get_tracer(name: str = "fastapi-clean-architecture") -> trace.Tracer:
    """Retrieve an OpenTelemetry Tracer instance.

    Args:
        name: Name of the instrumented component.

    Returns:
        trace.Tracer instance.
    """
    provider = get_tracer_provider()
    if isinstance(provider, TracerProvider) and getattr(provider, "_is_shutdown", False):
        init_tracer()
    return trace.get_tracer(name)


def get_current_trace_and_span_id() -> tuple[str, str]:
    """Extract active 32-hex trace_id and 16-hex span_id from current execution context.

    Returns:
        Tuple of (trace_id_hex, span_id_hex). Returns empty strings if no valid span is active.
    """
    current_span = trace.get_current_span()
    context = current_span.get_span_context()
    if context.is_valid:
        return format_trace_id(context.trace_id), format_span_id(context.span_id)
    return "", ""


def trace_span(
    name: str,
    attributes: dict[str, Any] | None = None,
) -> Callable[[F], F]:
    """Decorator to instrument synchronous or asynchronous functions with an OpenTelemetry Span.

    Captures execution duration, records contextual attributes, and automatically marks
    the span as ERROR while recording full exception details on failure.

    Args:
        name: Name of the span to create.
        attributes: Key-value attributes to attach to the span.

    Returns:
        Decorated callable.
    """

    def decorator(func: F) -> F:
        tracer = get_tracer("app.tracing.decorator")

        if inspect.iscoroutinefunction(func):

            @functools.wraps(func)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                with tracer.start_as_current_span(name) as span:
                    if attributes:
                        for k, v in attributes.items():
                            span.set_attribute(k, v)
                    try:
                        return await func(*args, **kwargs)
                    except Exception as exc:
                        span.record_exception(exc)
                        span.set_status(StatusCode.ERROR, str(exc))
                        raise

            return async_wrapper  # type: ignore[return-value]

        @functools.wraps(func)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            with tracer.start_as_current_span(name) as span:
                if attributes:
                    for k, v in attributes.items():
                        span.set_attribute(k, v)
                try:
                    return func(*args, **kwargs)
                except Exception as exc:
                    span.record_exception(exc)
                    span.set_status(StatusCode.ERROR, str(exc))
                    raise

        return sync_wrapper  # type: ignore[return-value]

    return decorator
