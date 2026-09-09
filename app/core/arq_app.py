"""ARQ (Async Redis Queue) configuration, settings, and worker lifespan hooks."""

from __future__ import annotations

from typing import Any

import httpx
from arq.connections import RedisSettings

from app.core.config import get_settings
from app.tasks.arq_tasks import broadcast_push_notification, sync_webhook_notification


def get_arq_redis_settings() -> RedisSettings:
    """Derive ARQ RedisSettings from the application's centralized configuration."""
    settings = get_settings()
    return RedisSettings.from_dsn(settings.redis_url)


async def startup(ctx: dict[str, Any]) -> None:
    """Initialize shared asynchronous network pools and HTTP clients in worker context."""
    ctx["http_client"] = httpx.AsyncClient(timeout=10.0)


async def shutdown(ctx: dict[str, Any]) -> None:
    """Gracefully close shared connection pools on worker shutdown."""
    client: httpx.AsyncClient | None = ctx.get("http_client")
    if client is not None:
        await client.aclose()


class WorkerSettings:
    """ARQ Worker configuration class defining concurrency, timeouts, and job registry."""

    functions = [
        sync_webhook_notification,
        broadcast_push_notification,
    ]
    redis_settings = get_arq_redis_settings()
    max_jobs: int = 100
    job_timeout: int = 60
    keep_result: int = 3600
    on_startup = startup
    on_shutdown = shutdown
