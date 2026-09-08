"""FastAPI Application Entrypoint."""

import time
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any

from fastapi import Depends, FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import (
    Base,
    engine,
    get_db_pool_status,
    get_db_session,
)
from app.core.exception_handlers import register_exception_handlers
from app.routers.user_router import router as user_router


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Lifespan context manager for application startup and shutdown events.

    Startup:
      - Initializes database tables via Base.metadata.create_all.
    Shutdown:
      - Closes and disposes the async database engine to release all pooled socket connections.
    """
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    await engine.dispose()


app = FastAPI(
    title="90-Day Production FastAPI Backend",
    description="Enterprise-grade FastAPI backend built with 3-Tier Clean Architecture and DSA optimization.",
    version="1.0.0",
    lifespan=lifespan,
)

# Register centralized exception handlers
register_exception_handlers(app)

# Mount feature routers
app.include_router(user_router)


@app.get("/health", tags=["Health"])
async def health_check() -> dict[str, str]:
    """Non-blocking health check endpoint to verify service liveness and responsiveness."""
    return {
        "status": "healthy",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/health/db", tags=["Health"])
async def db_health_check(
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """Database connectivity verification endpoint executing an async raw query.

    Executes 'SELECT 1' against the async engine, measures round-trip ping latency,
    and returns database status.
    """
    start_time = time.perf_counter()
    result = await session.scalar(select(1))
    latency_ms = round((time.perf_counter() - start_time) * 1000, 2)
    return {
        "status": "healthy",
        "database": "connected",
        "latency_ms": latency_ms,
        "scalar_result": result,
    }


@app.get("/health/db/pool", tags=["Health"])
async def db_pool_health_check() -> dict[str, Any]:
    """Database connection pool telemetry probe for observability and alerting.

    Returns pool utilization metrics including pool size, active checked-out connections,
    idle checked-in connections, and overflow capacity.
    """
    return get_db_pool_status()


@app.get("/test/raise-unhandled-500", include_in_schema=False)
async def raise_unhandled_500() -> None:
    """Internal test route to verify unhandled 500 error masking and trace ID generation."""
    raise RuntimeError(
        "Simulated unhandled internal database crash with secret credentials: db_pass=SuperSecret!"
    )
