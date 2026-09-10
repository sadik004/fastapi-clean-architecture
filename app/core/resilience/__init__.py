"""Resilience and Fault Tolerance Module."""

from app.core.resilience.backoff import calculate_backoff, retry_with_backoff
from app.core.resilience.bulkhead import Bulkhead
from app.core.resilience.circuit_breaker import CircuitBreaker, CircuitState
from app.core.resilience.fallback import DegradationLevel, FallbackEngine

__all__ = [
    "Bulkhead",
    "CircuitBreaker",
    "CircuitState",
    "DegradationLevel",
    "FallbackEngine",
    "calculate_backoff",
    "retry_with_backoff",
]
