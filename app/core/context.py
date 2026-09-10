"""Asyncio ContextVars for distributed correlation ID and request context propagation.

Guarantees thread-safe and coroutine-isolated context tracking across the ASGI call stack
in strictly O(1) time and memory overhead.
"""

from __future__ import annotations

from contextvars import ContextVar, Token

# Global ContextVar for distributed correlation ID
correlation_id_ctx: ContextVar[str] = ContextVar("correlation_id", default="")


def get_correlation_id() -> str:
    """Retrieve the current active correlation ID from asyncio context.

    Returns:
        The active correlation ID string, or empty string if unset.
    """
    return correlation_id_ctx.get()


def set_correlation_id(cid: str) -> Token[str]:
    """Bind a correlation ID to the current asyncio task context.

    Args:
        cid: The unique correlation ID string.

    Returns:
        A contextvars Token for deterministic reset/unbinding.
    """
    return correlation_id_ctx.set(cid)


def reset_correlation_id(token: Token[str]) -> None:
    """Reset the correlation ID in the current asyncio task context using a token.

    Args:
        token: The Token returned by set_correlation_id.
    """
    correlation_id_ctx.reset(token)
