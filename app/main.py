"""FastAPI Application Entrypoint."""

from datetime import datetime, timezone
from fastapi import FastAPI
from app.core.exception_handlers import register_exception_handlers
from app.routers.user_router import router as user_router

app = FastAPI(
    title="90-Day Production FastAPI Backend",
    description="Enterprise-grade FastAPI backend built with 3-Tier Clean Architecture and DSA optimization.",
    version="1.0.0",
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


@app.get("/test/raise-unhandled-500", include_in_schema=False)
async def raise_unhandled_500() -> None:
    """Internal test route to verify unhandled 500 error masking and trace ID generation."""
    raise RuntimeError(
        "Simulated unhandled internal database crash with secret credentials: db_pass=SuperSecret!"
    )
