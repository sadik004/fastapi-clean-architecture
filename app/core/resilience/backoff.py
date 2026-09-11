"""Exponential Backoff with Jitter Algorithms (AWS Canonical Implementation).

Provides:
1. calculate_backoff: O(1) mathematical calculation for Full Jitter, Equal Jitter,
   Decorrelated Jitter, and deterministic No-Jitter.
2. retry_with_backoff: Asynchronous decorator and execution wrapper for resilient
   retries on transient failures, defeating the Thundering Herd / Retry Storm problem.
"""

from __future__ import annotations

import asyncio
import functools
import logging
import random
from collections.abc import Awaitable, Callable
from typing import Any, ParamSpec, TypeVar

from app.core.exceptions import ServiceUnavailableException

logger = logging.getLogger("app.resilience.backoff")

P = ParamSpec("P")
R = TypeVar("R")


def calculate_backoff(
    attempt: int,
    base_delay: float = 1.0,
    max_delay: float = 60.0,
    strategy: str = "full_jitter",
    previous_delay: float | None = None,
) -> float:
    """Calculate the sleep delay in seconds using exponential backoff and jitter algorithms.

    Algorithms implemented (Canonical AWS Architecture):
    1. full_jitter:
       temp = min(max_delay, base_delay * 2^attempt)
       sleep = uniform(0, temp)
       Maximizes traffic spread across time, completely breaking synchronized retry stampedes.

    2. equal_jitter:
       temp = min(max_delay, base_delay * 2^attempt)
       sleep = (temp / 2) + uniform(0, temp / 2)
       Guarantees some minimum sleep while randomizing the remaining half.

    3. decorrelated_jitter:
       prev = previous_delay if previous_delay is not None else base_delay
       sleep = min(max_delay, uniform(base_delay, prev * 3))
       Dynamically scales subsequent delays decorrelated from attempt index.

    4. no_jitter:
       sleep = min(max_delay, base_delay * 2^attempt)
       Deterministic exponential growth (used for baseline comparison & benchmarking).

    Args:
        attempt: Zero- or one-indexed retry attempt number (clamped >= 0).
        base_delay: Initial starting delay in seconds (> 0.0).
        max_delay: Absolute upper bound cap in seconds (>= base_delay).
        strategy: 'full_jitter', 'equal_jitter', 'decorrelated_jitter', or 'no_jitter'.
        previous_delay: Delay calculated in prior attempt (required for decorrelated_jitter).

    Returns:
        float: Calculated delay duration in seconds, strictly guaranteed in O(1) time.
    """
    safe_attempt = max(0, attempt)
    safe_base = max(0.0001, base_delay)
    safe_max = max(safe_base, max_delay)

    # Clamp attempt exponent to 30 to prevent math overflow before capping
    exp_factor = 2 ** min(safe_attempt, 30)
    temp = min(safe_max, safe_base * exp_factor)

    normalized_strategy = strategy.lower().strip()

    if normalized_strategy == "full_jitter":
        return float(random.uniform(0.0, temp))  # noqa: S311

    if normalized_strategy == "equal_jitter":
        half = temp / 2.0
        return float(half + random.uniform(0.0, half))  # noqa: S311

    if normalized_strategy == "decorrelated_jitter":
        prev = previous_delay if (previous_delay is not None and previous_delay >= safe_base) else safe_base
        return float(min(safe_max, random.uniform(safe_base, prev * 3.0)))  # noqa: S311

    if normalized_strategy == "no_jitter":
        return float(temp)

    raise ValueError(
        f"Unsupported backoff strategy: '{strategy}'. "
        "Choose from 'full_jitter', 'equal_jitter', 'decorrelated_jitter', or 'no_jitter'."
    )


def retry_with_backoff(  # noqa: UP047
    func: Callable[P, Awaitable[R]] | None = None,
    *,
    max_retries: int = 3,
    base_delay: float = 0.1,
    max_delay: float = 5.0,
    strategy: str = "full_jitter",
    retry_exceptions: tuple[type[Exception], ...] = (ServiceUnavailableException, TimeoutError),
    on_retry: Callable[[int, float, Exception], None] | None = None,
) -> Callable[..., Any]:
    """Decorator to wrap asynchronous coroutines with exponential backoff and randomized jitter.

    Supports both:
    @retry_with_backoff
    async def foo(): ...

    and:
    @retry_with_backoff(max_retries=5, base_delay=0.05, strategy="full_jitter")
    async def foo(): ...
    """

    def decorator(fn: Callable[P, Awaitable[R]]) -> Callable[P, Awaitable[R]]:
        @functools.wraps(fn)
        async def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            last_delay: float | None = None
            for attempt in range(max_retries + 1):
                try:
                    return await fn(*args, **kwargs)
                except retry_exceptions as exc:
                    if attempt >= max_retries:
                        logger.warning(
                            "Max retries (%d) exhausted for '%s': %s",
                            max_retries,
                            fn.__name__,
                            exc,
                        )
                        raise

                    delay = calculate_backoff(
                        attempt=attempt,
                        base_delay=base_delay,
                        max_delay=max_delay,
                        strategy=strategy,
                        previous_delay=last_delay,
                    )
                    last_delay = delay

                    if on_retry is not None:
                        try:
                            on_retry(attempt + 1, delay, exc)
                        except Exception as cb_err:
                            logger.error("Error in on_retry callback: %s", cb_err)

                    logger.info(
                        "Transient failure in '%s' (%s: %s). Retrying in %.4fs (attempt %d/%d, strategy=%s)...",
                        fn.__name__,
                        type(exc).__name__,
                        exc,
                        delay,
                        attempt + 1,
                        max_retries,
                        strategy,
                    )
                    if delay > 0.0:
                        await asyncio.sleep(delay)

            # Fallback (should not be reached due to raise above)
            raise RuntimeError(f"Retry loop unexpectedly terminated for {fn.__name__}")

        return wrapper

    if func is not None:
        return decorator(func)
    return decorator


__all__ = ["calculate_backoff", "retry_with_backoff"]
