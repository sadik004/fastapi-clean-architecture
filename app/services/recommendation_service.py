"""Resilient Product Recommendation Service with Multi-Tier Graceful Degradation."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from app.core.exceptions import ServiceUnavailableException
from app.core.resilience.fallback import DegradationLevel, FallbackEngine

logger = logging.getLogger("app.services.recommendations")

# Global curated static baseline: Safe fallback when primary AI and Redis cache are down
DEFAULT_TRENDING_PRODUCTS: list[dict[str, Any]] = [
    {
        "product_id": "prod_trend_1",
        "title": "Wireless Noise-Cancelling Headphones",
        "price": 149.99,
        "category": "Audio",
        "score": 0.99,
    },
    {
        "product_id": "prod_trend_2",
        "title": "Ergonomic Mechanical Keyboard",
        "price": 119.50,
        "category": "Peripherals",
        "score": 0.97,
    },
    {
        "product_id": "prod_trend_3",
        "title": "Ultra-Wide 4K Gaming Monitor",
        "price": 499.00,
        "category": "Displays",
        "score": 0.95,
    },
    {
        "product_id": "prod_trend_4",
        "title": "Smart Fitness Tracker Band",
        "price": 59.99,
        "category": "Wearables",
        "score": 0.93,
    },
    {
        "product_id": "prod_trend_5",
        "title": "USB-C Multi-Port Hub",
        "price": 39.99,
        "category": "Accessories",
        "score": 0.90,
    },
]


class ProductRecommendationService:
    """Service providing personalized product recommendations protected by multi-tier fallback."""

    __slots__ = ("_fallback_engine",)

    def __init__(
        self,
        fallback_engine: FallbackEngine | None = None,
        redis_client: Any = None,
    ) -> None:
        if fallback_engine is not None:
            self._fallback_engine = fallback_engine
        elif redis_client is not None:
            self._fallback_engine = FallbackEngine(redis_client=redis_client)
        else:
            self._fallback_engine = FallbackEngine()

    async def get_personalized_recommendations(
        self,
        user_id: int,
        simulate_failure: bool = False,
    ) -> tuple[dict[str, Any], DegradationLevel]:
        """Fetch personalized product recommendations with multi-tier graceful degradation.

        Progression:
        - Tier 1: Primary call to simulated external AI recommendation engine.
        - Tier 2: Stale cached copy from Redis (`recs:user:{user_id}`).
        - Tier 3: Static curated trending products (`DEFAULT_TRENDING_PRODUCTS`).
        """
        cache_key = f"recs:user:{user_id}"

        async def primary_ai_call() -> list[dict[str, Any]]:
            if simulate_failure:
                raise ServiceUnavailableException(
                    message=f"External AI Recommendation Service failed for user {user_id}",
                    code="DOWNSTREAM_TEMPORARILY_UNAVAILABLE",
                    retry_after=5,
                )

            # Simulated live personalized computation
            return [
                {
                    "product_id": f"prod_ai_{user_id}_{idx}",
                    "title": f"AI Curated Recommendation #{idx} for User {user_id}",
                    "price": round(29.99 + (user_id % 7) * 4.5 + idx * 8.0, 2),
                    "category": "Personalized Recommendations",
                    "score": round(0.99 - idx * 0.04, 2),
                }
                for idx in range(1, 4)
            ]

        raw_items, level = await self._fallback_engine.execute_with_fallback(
            primary_func=primary_ai_call,
            fallback_cache_key=cache_key,
            static_default=DEFAULT_TRENDING_PRODUCTS,
            cache_ttl=3600,
        )

        response_payload = {
            "user_id": user_id,
            "items": raw_items,
            "degraded": level != DegradationLevel.PRIMARY,
            "degradation_level": level.value,
            "served_at": datetime.now(UTC),
        }

        return response_payload, level

    async def seed_user_cache(
        self,
        user_id: int,
        items: list[dict[str, Any]],
        ttl_seconds: int = 3600,
    ) -> str:
        """Helper to prime the Redis fallback cache with specific items for testing Tier 2."""
        cache_key = f"recs:user:{user_id}"
        await self._fallback_engine._safe_cache_set(cache_key, items, ttl_seconds)
        return cache_key


_global_recommendation_service = ProductRecommendationService()


def get_recommendation_service() -> ProductRecommendationService:
    """FastAPI dependency provider yielding the shared ProductRecommendationService singleton."""
    return _global_recommendation_service


__all__ = [
    "DEFAULT_TRENDING_PRODUCTS",
    "ProductRecommendationService",
    "get_recommendation_service",
]
