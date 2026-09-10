"""
Tests for Graceful Degradation & Multi-Tier Fallback Architecture (Day 64).

Validates:
1. FallbackEngine 3-tier fallback progression:
   - Tier 1: Primary live execution -> DegradationLevel.PRIMARY + safe cache write.
   - Tier 2: Stale cache fallback -> DegradationLevel.STALE_CACHE.
   - Tier 3: Static default fallback -> DegradationLevel.STATIC_DEFAULT (Zero 500 guarantee).
2. Cache fault-tolerance: Cache read/write failures do not crash primary execution or fallbacks.
3. ProductRecommendationService personalization and fallback handling.
4. HTTP Endpoints:
   - Telemetry headers: X-Degraded-Mode and X-Degradation-Level.
   - Seed cache endpoint.
"""

from typing import Any
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.resilience.fallback import DegradationLevel, FallbackEngine
from app.main import app
from app.services.recommendation_service import (
    DEFAULT_TRENDING_PRODUCTS,
    ProductRecommendationService,
)


@pytest.fixture
def mock_redis() -> AsyncMock:
    """Mock Redis client with in-memory hash store for tests."""
    store: dict[str, str] = {}
    redis = AsyncMock()

    async def _get(key: str) -> str | None:
        return store.get(key)

    async def _set(key: str, value: str, ex: int | None = None) -> bool:
        store[key] = value
        return True

    redis.get.side_effect = _get
    redis.set.side_effect = _set
    redis._store = store
    return redis


# ============================================================================
# Unit Tests: FallbackEngine
# ============================================================================


@pytest.mark.asyncio
async def test_fallback_engine_tier1_primary_success(mock_redis: AsyncMock) -> None:
    """Tier 1: Primary func succeeds, returns PRIMARY level and updates cache."""
    engine = FallbackEngine(redis_client=mock_redis)

    async def primary_fn() -> list[dict[str, Any]]:
        return [{"product_id": "prod-1", "title": "Mechanical Keyboard", "price": 99.99}]

    data, level = await engine.execute_with_fallback(
        primary_func=primary_fn,
        fallback_cache_key="recs:user:1",
        static_default=[{"product_id": "default", "title": "Default", "price": 0.0}],
        cache_ttl=3600,
    )

    assert level == DegradationLevel.PRIMARY
    assert len(data) == 1
    assert data[0]["title"] == "Mechanical Keyboard"
    # Verify cached
    mock_redis.set.assert_awaited_once()


@pytest.mark.asyncio
async def test_fallback_engine_tier2_stale_cache_on_primary_failure(
    mock_redis: AsyncMock,
) -> None:
    """Tier 2: Primary func fails, stale cached data is returned with STALE_CACHE level."""
    # Pre-seed cache
    mock_redis._store["recs:user:2"] = (
        '[{"product_id": "cached-1", "title": "Cached Mouse", "price": 49.99}]'
    )
    engine = FallbackEngine(redis_client=mock_redis)

    async def failing_primary() -> list[dict[str, Any]]:
        raise ConnectionResetError("Downstream service timed out")

    data, level = await engine.execute_with_fallback(
        primary_func=failing_primary,
        fallback_cache_key="recs:user:2",
        static_default=[{"product_id": "default", "title": "Default", "price": 0.0}],
        cache_ttl=3600,
    )

    assert level == DegradationLevel.STALE_CACHE
    assert len(data) == 1
    assert data[0]["title"] == "Cached Mouse"


@pytest.mark.asyncio
async def test_fallback_engine_tier3_static_default_on_cache_miss(
    mock_redis: AsyncMock,
) -> None:
    """Tier 3: Primary fails and cache is empty, static default is returned with STATIC_DEFAULT."""
    engine = FallbackEngine(redis_client=mock_redis)

    async def failing_primary() -> list[dict[str, Any]]:
        raise TimeoutError("Third-party recommendation microservice unavailable")

    static_default = [{"product_id": "default-1", "title": "Standard Laptop", "price": 799.0}]

    data, level = await engine.execute_with_fallback(
        primary_func=failing_primary,
        fallback_cache_key="recs:user:9999",
        static_default=static_default,
        cache_ttl=3600,
    )

    assert level == DegradationLevel.STATIC_DEFAULT
    assert data == static_default


@pytest.mark.asyncio
async def test_fallback_engine_tier3_when_redis_is_down(mock_redis: AsyncMock) -> None:
    """Tier 3: When Redis throws connection error, fallback engine safely returns static default."""
    mock_redis.get.side_effect = ConnectionError("Redis cluster unreachable")
    engine = FallbackEngine(redis_client=mock_redis)

    async def failing_primary() -> list[dict[str, Any]]:
        raise RuntimeError("ML model inferencing failed")

    static_default = [{"product_id": "default-fallback", "title": "Safe Default", "price": 10.0}]

    data, level = await engine.execute_with_fallback(
        primary_func=failing_primary,
        fallback_cache_key="recs:user:redis-down",
        static_default=static_default,
    )

    assert level == DegradationLevel.STATIC_DEFAULT
    assert data == static_default


@pytest.mark.asyncio
async def test_fallback_engine_cache_write_failure_tolerance(
    mock_redis: AsyncMock,
) -> None:
    """Primary succeeds even if Redis write throws an error (silent cache degradation)."""
    mock_redis.set.side_effect = TimeoutError("Redis write socket timeout")
    engine = FallbackEngine(redis_client=mock_redis)

    async def primary_fn() -> list[dict[str, Any]]:
        return [{"product_id": "prod-ok", "title": "Monitor", "price": 250.0}]

    fallback_default: list[dict[str, Any]] = []
    data, level = await engine.execute_with_fallback(
        primary_func=primary_fn,
        fallback_cache_key="recs:user:write-fail",
        static_default=fallback_default,
    )

    assert level == DegradationLevel.PRIMARY
    assert data[0]["title"] == "Monitor"


@pytest.mark.asyncio
async def test_fallback_engine_without_cache_key() -> None:
    """FallbackEngine functions seamlessly when fallback_cache_key is None."""
    engine = FallbackEngine(redis_client=None)

    async def failing_primary() -> str:
        raise ValueError("Something went wrong")

    data, level = await engine.execute_with_fallback(
        primary_func=failing_primary,
        fallback_cache_key=None,
        static_default="safe-fallback-string",
    )

    assert level == DegradationLevel.STATIC_DEFAULT
    assert data == "safe-fallback-string"


# ============================================================================
# Service Layer Tests: ProductRecommendationService
# ============================================================================


@pytest.mark.asyncio
async def test_recommendation_service_primary_flow(mock_redis: AsyncMock) -> None:
    """ProductRecommendationService returns live personalized recommendations."""
    service = ProductRecommendationService(redis_client=mock_redis)
    result, level = await service.get_personalized_recommendations(
        user_id=101, simulate_failure=False
    )

    assert level == DegradationLevel.PRIMARY
    assert result["degraded"] is False
    assert result["degradation_level"] == "PRIMARY"
    assert len(result["items"]) == 3
    assert "101" in result["items"][0]["title"]


@pytest.mark.asyncio
async def test_recommendation_service_stale_cache_flow(mock_redis: AsyncMock) -> None:
    """ProductRecommendationService falls back to pre-seeded cache on simulated failure."""
    service = ProductRecommendationService(redis_client=mock_redis)
    seeded_items = [
        {
            "product_id": "seed-100",
            "title": "Seeded Headphones",
            "price": 199.99,
            "category": "Audio",
            "score": 0.98,
        }
    ]
    await service.seed_user_cache(user_id=102, items=seeded_items, ttl_seconds=600)

    result, level = await service.get_personalized_recommendations(
        user_id=102, simulate_failure=True
    )

    assert level == DegradationLevel.STALE_CACHE
    assert result["degraded"] is True
    assert result["degradation_level"] == "STALE_CACHE"
    assert result["items"] == seeded_items


@pytest.mark.asyncio
async def test_recommendation_service_static_default_flow(
    mock_redis: AsyncMock,
) -> None:
    """ProductRecommendationService falls back to DEFAULT_TRENDING_PRODUCTS when empty."""
    service = ProductRecommendationService(redis_client=mock_redis)

    result, level = await service.get_personalized_recommendations(
        user_id=999, simulate_failure=True
    )

    assert level == DegradationLevel.STATIC_DEFAULT
    assert result["degraded"] is True
    assert result["degradation_level"] == "STATIC_DEFAULT"
    assert result["items"] == DEFAULT_TRENDING_PRODUCTS


# ============================================================================
# Integration & HTTP Endpoint Tests
# ============================================================================


@pytest.mark.asyncio
async def test_endpoint_recommendations_tier1_primary(fake_redis: Any) -> None:
    """GET /resilience/recommendations/{user_id} returns PRIMARY headers."""
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        response = await ac.get(
            "/resilience/recommendations/1", params={"simulate_failure": False}
        )

    assert response.status_code == 200
    assert response.headers.get("X-Degraded-Mode") == "FALSE"
    assert response.headers.get("X-Degradation-Level") == "PRIMARY"
    body = response.json()
    assert body["degradation_level"] == "PRIMARY"
    assert body["degraded"] is False
    assert len(body["items"]) > 0


@pytest.mark.asyncio
async def test_endpoint_recommendations_tier2_stale_cache(fake_redis: Any) -> None:
    """POST seed-cache, then GET with simulate_failure=true returns STALE_CACHE."""
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # 1. Seed cache for user 202
        seed_payload = {
            "user_id": 202,
            "items": [
                {
                    "product_id": "item-bob-1",
                    "title": "Bob's Cached Keyboard",
                    "price": 89.0,
                    "category": "Electronics",
                    "score": 0.95,
                }
            ],
            "ttl_seconds": 1800,
        }
        seed_resp = await ac.post(
            "/resilience/recommendations/seed-cache", json=seed_payload
        )
        assert seed_resp.status_code == 200
        assert seed_resp.json()["status"] == "seeded"

        # 2. Query with simulate_failure=true
        response = await ac.get(
            "/resilience/recommendations/202", params={"simulate_failure": True}
        )

    assert response.status_code == 200
    assert response.headers.get("X-Degraded-Mode") == "TRUE"
    assert response.headers.get("X-Degradation-Level") == "STALE_CACHE"
    body = response.json()
    assert body["degradation_level"] == "STALE_CACHE"
    assert body["degraded"] is True
    assert body["items"][0]["title"] == "Bob's Cached Keyboard"


@pytest.mark.asyncio
async def test_endpoint_recommendations_tier3_static_default(fake_redis: Any) -> None:
    """GET with simulate_failure=true for fresh unseeded user returns STATIC_DEFAULT."""
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        response = await ac.get(
            "/resilience/recommendations/99999",
            params={"simulate_failure": True},
        )

    assert response.status_code == 200
    assert response.headers.get("X-Degraded-Mode") == "TRUE"
    assert response.headers.get("X-Degradation-Level") == "STATIC_DEFAULT"
    body = response.json()
    assert body["degradation_level"] == "STATIC_DEFAULT"
    assert body["degraded"] is True
    assert len(body["items"]) == len(DEFAULT_TRENDING_PRODUCTS)
    assert body["items"][0]["product_id"] == DEFAULT_TRENDING_PRODUCTS[0]["product_id"]
