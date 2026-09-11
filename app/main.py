"""FastAPI Application Entrypoint."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select

from app.core.config import get_settings
from app.core.database import (
    async_session_factory,
    engine,
    primary_engine,
    replica_engine,
)
from app.core.dependencies import RateLimitGuard, TokenBucketGuard
from app.core.exception_handlers import register_exception_handlers
from app.core.kafka import close_kafka_producer, init_kafka_producer
from app.core.lifecycle import get_shutdown_manager
from app.core.logging import setup_logging
from app.core.middleware import CustomSecurityAndObservabilityMiddleware
from app.core.rabbitmq import close_rabbitmq, init_rabbitmq
from app.core.redis import (
    close_redis_pool,
    init_redis_pool,
)
from app.core.tracing import init_tracer, shutdown_tracer
from app.events.handlers import register_default_event_handlers
from app.repositories.sqlalchemy_user_repository import SqlAlchemyUserRepository
from app.routers.arq_router import router as arq_router
from app.routers.auth_router import router as auth_router
from app.routers.broker_router import router as broker_router
from app.routers.catalog_router import router as catalog_router
from app.routers.dashboard_router import router as dashboard_router
from app.routers.database_admin_router import router as database_admin_router
from app.routers.dlq_router import router as dlq_router
from app.routers.document_router import router as document_router
from app.routers.health_router import router as health_router
from app.routers.job_router import router as job_router
from app.routers.kafka_consumer_router import router as kafka_consumer_router
from app.routers.kafka_router import router as kafka_router
from app.routers.leaderboard_router import router as leaderboard_router
from app.routers.ledger_router import router as ledger_router
from app.routers.metrics_router import router as metrics_router
from app.routers.observability_router import router as observability_router
from app.routers.order_router import router as order_router
from app.routers.outbox_router import router as outbox_router
from app.routers.partition_router import router as partition_router
from app.routers.payment_router import router as payment_router
from app.routers.product_router import router as product_router
from app.routers.profiling_router import router as profiling_router
from app.routers.replica_router import router as replica_router
from app.routers.resilience_router import router as resilience_router
from app.routers.schedule_router import router as schedule_router
from app.routers.search_router import router as search_router
from app.routers.security_audit_router import router as security_audit_router
from app.routers.security_router import router as security_router
from app.routers.task_router import router as task_router
from app.routers.tracing_router import router as tracing_router
from app.routers.user_router import router as user_router
from app.services.user_service import seed_user_bloom_filter


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    """Lifespan context manager for application startup and shutdown events.

    Startup:
      - Verifies database connectivity using non-blocking ping query (SELECT 1).
      - In production, DDL schema management is strictly delegated to version-controlled
        Alembic migrations rather than un-versioned Base.metadata.create_all().
      - Initializes async Redis connection pool and verifies connectivity.
      - Seeds in-memory Bloom Filter with existing user IDs to prevent cache penetration.
      - Initializes RabbitMQ AMQP 0-9-1 connection and channel.
      - Initializes Apache Kafka event streaming producer.
    Shutdown:
      - Gracefully closes Kafka producer and flushes pending record batches.
      - Gracefully closes RabbitMQ connection and channel.
      - Gracefully closes Redis connection pool and releases socket descriptors.
      - Closes and disposes the async database engine to release all pooled socket connections.
    """
    async with engine.connect() as conn:
        await conn.scalar(select(1))
    await init_redis_pool()
    await init_rabbitmq()
    await init_kafka_producer()
    register_default_event_handlers()

    # Seed User Bloom Filter from persistent database repository
    async with async_session_factory() as session:
        repo = SqlAlchemyUserRepository(session=session)
        await seed_user_bloom_filter(repo)

    # Initialize OpenTelemetry distributed tracing provider
    init_tracer(environment=settings.environment)

    # Register POSIX signal handlers for graceful shutdown
    shutdown_mgr = get_shutdown_manager()
    shutdown_mgr.register_signal_handlers()

    yield
    # Phase 2: Wait for in-flight requests to complete before closing connection pools
    await shutdown_mgr.wait_for_drain()

    # Phase 3: State and pool disposal
    shutdown_tracer()
    await close_kafka_producer()
    await close_rabbitmq()
    await close_redis_pool()
    await primary_engine.dispose()
    await replica_engine.dispose()


app = FastAPI(
    title="90-Day Production FastAPI Backend",
    description="Enterprise-grade FastAPI backend built with 3-Tier Clean Architecture and DSA optimization.",
    version="1.0.0",
    lifespan=lifespan,
)

# Register centralized exception handlers
register_exception_handlers(app)

settings = get_settings()

# Initialize enterprise structured logging pipeline
setup_logging(environment=settings.environment)

# Initialize OpenTelemetry distributed tracing provider immediately
init_tracer(environment=settings.environment)

# Register global custom security and observability middleware
app.add_middleware(CustomSecurityAndObservabilityMiddleware)

# Configure strict production CORS middleware (Zero-wildcard when credentials enabled)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=[
        "Authorization",
        "Content-Type",
        "X-API-Key",
        "X-Tenant-ID",
        "X-Department",
        "X-Client-IP",
        "X-Business-Hours",
        "Idempotency-Key",
        "X-Request-ID",
        "X-Correlation-ID",
        "traceparent",
        "tracestate",
    ],
)

# Mount feature routers
app.include_router(auth_router)
app.include_router(user_router)
app.include_router(job_router)
app.include_router(metrics_router)
app.include_router(leaderboard_router)
app.include_router(product_router)
app.include_router(order_router)
app.include_router(payment_router)
app.include_router(document_router)
app.include_router(security_router)
app.include_router(task_router)
app.include_router(schedule_router)
app.include_router(arq_router)
app.include_router(broker_router)
app.include_router(kafka_router)
app.include_router(kafka_consumer_router)
app.include_router(dlq_router)
app.include_router(outbox_router)
app.include_router(resilience_router)
app.include_router(replica_router)
app.include_router(catalog_router)
app.include_router(database_admin_router)
app.include_router(partition_router)
app.include_router(search_router)
app.include_router(observability_router)
app.include_router(tracing_router)
app.include_router(security_audit_router)
app.include_router(profiling_router)
app.include_router(dashboard_router)
app.include_router(health_router)
app.include_router(ledger_router)


@app.get(
    "/test-rate-limit/distributed",
    dependencies=[Depends(RateLimitGuard(limit=5, window_seconds=10.0, scope="test"))],
    tags=["Testing"],
    summary="Protected test endpoint for distributed sliding window rate limiter",
)
async def distributed_rate_limit_test_root(request: Request) -> dict[str, Any]:
    """Protected test route under root prefix."""
    return {
        "message": "Request accepted within distributed sliding window quota",
        "client_id": getattr(request.state, "rate_limit_client", "unknown"),
        "request_number": getattr(request.state, "rate_limit_count", 1),
    }


@app.get(
    "/test-rate-limit/token-bucket",
    dependencies=[Depends(TokenBucketGuard(capacity=5.0, refill_rate=1.0, scope="test"))],
    tags=["Testing"],
    summary="Protected test endpoint for Token Bucket rate limiter",
)
async def token_bucket_test_root(request: Request) -> dict[str, Any]:
    """Protected test route under root prefix for Token Bucket."""
    return {
        "message": "Request accepted within token bucket quota",
        "client_id": getattr(request.state, "token_bucket_client", "unknown"),
        "remaining_tokens": getattr(request.state, "token_bucket_remaining", 0.0),
        "capacity": getattr(request.state, "token_bucket_capacity", 5.0),
    }


@app.get("/test/raise-unhandled-500", include_in_schema=False)
async def raise_unhandled_500() -> None:
    """Internal test route to verify unhandled 500 error masking and trace ID generation."""
    raise RuntimeError("Simulated unhandled internal database crash with secret credentials: db_pass=SuperSecret!")
