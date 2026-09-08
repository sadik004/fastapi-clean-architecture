"""FastAPI Application Entrypoint."""

from datetime import datetime, timezone
from fastapi import FastAPI
from app.routers.user_router import router as user_router

app = FastAPI(
    title="90-Day Production FastAPI Backend",
    description="Enterprise-grade FastAPI backend built with 3-Tier Clean Architecture and DSA optimization.",
    version="1.0.0",
)

# Mount feature routers
app.include_router(user_router)


@app.get("/health", tags=["Health"])
async def health_check() -> dict[str, str]:
    """Non-blocking health check endpoint to verify service liveness and responsiveness."""
    return {
        "status": "healthy",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

