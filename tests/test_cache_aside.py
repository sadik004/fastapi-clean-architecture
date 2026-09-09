"""Integration and unit tests for Day 32: Cache-Aside (Lazy Loading) Pattern & Invalidation."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from app.core.exceptions import UserNotFoundException
from app.repositories.user_repository import InMemoryUserRepository
from app.schemas.user import UserCreate, UserProfileUpdate
from app.services.cache_service import CacheService
from app.services.user_service import (
    CACHE_USER_PREFIX,
    CACHE_USER_TTL_SECONDS,
    UserService,
)


@pytest.mark.asyncio
async def test_cache_aside_miss_populates_redis(fake_redis: Any) -> None:
    """Test 1: Cache Miss on initial read queries repository and lazily loads entity into Redis with TTL."""
    repo = InMemoryUserRepository()
    cache_service = CacheService(redis_client=fake_redis)
    await cache_service.reset_metrics()
    user_service = UserService(repository=repo, cache_service=cache_service)

    created_user = await user_service.register_user(
        UserCreate(
            email="cache_miss@example.com",
            username="cache_miss_user",
            password="SecurePassword123!",
            password_confirm="SecurePassword123!",
            age=30,
        )
    )

    cache_key = f"{CACHE_USER_PREFIX}{created_user.id}"
    # Verify key does not exist before first read
    assert await fake_redis.exists(cache_key) == 0

    # First read: cache miss
    fetched_user = await user_service.get_user_by_id(created_user.id)
    assert fetched_user.id == created_user.id
    assert fetched_user.username == "cache_miss_user"

    # Verify key is populated in Redis with TTL <= 300
    assert await fake_redis.exists(cache_key) == 1
    ttl = await fake_redis.ttl(cache_key)
    assert 0 < ttl <= CACHE_USER_TTL_SECONDS

    # Verify telemetry recorded 1 miss and 0 hits
    metrics = await cache_service.get_metrics()
    assert metrics["misses"] == 1
    assert metrics["hits"] == 0
    assert metrics["hit_ratio"] == 0.0


@pytest.mark.asyncio
async def test_cache_aside_hit_avoids_database_query(fake_redis: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test 2: Cache Hit on subsequent read returns cached entity without invoking repository."""
    repo = InMemoryUserRepository()
    cache_service = CacheService(redis_client=fake_redis)
    await cache_service.reset_metrics()
    user_service = UserService(repository=repo, cache_service=cache_service)

    user = await user_service.register_user(
        UserCreate(
            email="cache_hit@example.com",
            username="cache_hit_user",
            password="SecurePassword123!",
            password_confirm="SecurePassword123!",
            age=28,
        )
    )

    # First read (Miss -> loads to cache)
    user_read_1 = await user_service.get_user_by_id(user.id)
    assert user_read_1.id == user.id

    # Spy on repo get_by_id
    original_get_by_id = repo.get_by_id
    repo_spy = AsyncMock(side_effect=original_get_by_id)
    monkeypatch.setattr(repo, "get_by_id", repo_spy)

    # Second read (Hit -> served from Redis)
    user_read_2 = await user_service.get_user_by_id(user.id)
    assert user_read_2.id == user.id
    assert user_read_2.email == "cache_hit@example.com"

    # Repository was NOT called during the cache hit
    assert repo_spy.call_count == 0

    # Telemetry should reflect 1 hit and 1 miss (50% hit ratio)
    metrics = await cache_service.get_metrics()
    assert metrics["hits"] == 1
    assert metrics["misses"] == 1
    assert metrics["hit_ratio"] == 0.5


@pytest.mark.asyncio
async def test_cache_invalidation_on_update(fake_redis: Any) -> None:
    """Test 3: Updating user attributes evicts the cache entry to prevent stale reads."""
    repo = InMemoryUserRepository()
    cache_service = CacheService(redis_client=fake_redis)
    user_service = UserService(repository=repo, cache_service=cache_service)

    user = await user_service.register_user(
        UserCreate(
            email="invalidate_update@example.com",
            username="update_user_test",
            password="SecurePassword123!",
            password_confirm="SecurePassword123!",
            age=22,
        )
    )
    cache_key = f"{CACHE_USER_PREFIX}{user.id}"

    # Prime cache via read
    await user_service.get_user_by_id(user.id)
    assert await fake_redis.exists(cache_key) == 1

    # Update user profile
    await user_service.update_profile(
        user.id,
        UserProfileUpdate(
            username="updated_name_test",
            bio="Updated bio information",
        ),
    )

    # Verify cache key was purged upon update
    assert await fake_redis.exists(cache_key) == 0

    # Subsequent read fetches new data from repository and caches it
    refreshed_user = await user_service.get_user_by_id(user.id)
    assert refreshed_user.username == "updated_name_test"
    assert refreshed_user.bio == "Updated bio information"
    assert await fake_redis.exists(cache_key) == 1


@pytest.mark.asyncio
async def test_cache_invalidation_on_delete(fake_redis: Any) -> None:
    """Test 4: Deleting a user purges the cache entry and subsequent reads raise UserNotFoundException."""
    repo = InMemoryUserRepository()
    cache_service = CacheService(redis_client=fake_redis)
    user_service = UserService(repository=repo, cache_service=cache_service)

    user = await user_service.register_user(
        UserCreate(
            email="delete_cache@example.com",
            username="delete_user_cache",
            password="SecurePassword123!",
            password_confirm="SecurePassword123!",
            age=35,
        )
    )
    cache_key = f"{CACHE_USER_PREFIX}{user.id}"

    # Prime cache
    await user_service.get_user_by_id(user.id)
    assert await fake_redis.exists(cache_key) == 1

    # Delete user
    await user_service.delete_user(user.id)

    # Verify cache key was evicted
    assert await fake_redis.exists(cache_key) == 0

    # Subsequent read raises UserNotFoundException
    with pytest.raises(UserNotFoundException):
        await user_service.get_user_by_id(user.id)


@pytest.mark.asyncio
async def test_cache_aside_graceful_fallback_on_redis_error() -> None:
    """Test 5: If Redis fails, UserService degrades gracefully to DB without throwing exceptions."""
    repo = InMemoryUserRepository()
    failing_redis = AsyncMock()
    failing_redis.get = AsyncMock(side_effect=ConnectionError("Redis connection dropped"))
    failing_redis.set = AsyncMock(side_effect=ConnectionError("Redis connection dropped"))

    cache_service = CacheService(redis_client=failing_redis)
    user_service = UserService(repository=repo, cache_service=cache_service)

    user = await user_service.register_user(
        UserCreate(
            email="fallback_user@example.com",
            username="fallback_user",
            password="SecurePassword123!",
            password_confirm="SecurePassword123!",
            age=40,
        )
    )

    # Should successfully return user from repo without raising Redis ConnectionError
    fetched_user = await user_service.get_user_by_id(user.id)
    assert fetched_user.id == user.id
    assert fetched_user.username == "fallback_user"


@pytest.mark.asyncio
async def test_cache_metrics_endpoint(fake_redis: Any, admin_auth_headers: dict[str, str]) -> None:
    """Test 6: GET /metrics/cache returns accurate hits, misses, and hit ratio over HTTP."""
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    cache_service = CacheService(redis_client=fake_redis)
    await cache_service.reset_metrics()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        # Initial metrics check
        res = await ac.get("/metrics/cache")
        assert res.status_code == 200
        data = res.json()
        assert "hits" in data
        assert "misses" in data
        assert "hit_ratio" in data
        assert data["hits"] == 0
        assert data["misses"] == 0
        assert data["hit_ratio"] == 0.0

        # Create a user
        create_res = await ac.post(
            "/users/",
            json={
                "email": "telemetry_user@example.com",
                "username": "telemetry_user",
                "password": "Password123!",
                "password_confirm": "Password123!",
                "age": 25,
                "role": "user",
            },
        )
        assert create_res.status_code == 201
        user_id = create_res.json()["id"]

        # First fetch: cache miss
        get_res_1 = await ac.get(f"/users/{user_id}", headers=admin_auth_headers)
        assert get_res_1.status_code == 200

        metrics_res_1 = await ac.get("/metrics/cache")
        assert metrics_res_1.status_code == 200
        metrics_data_1 = metrics_res_1.json()
        assert metrics_data_1["misses"] == 1
        assert metrics_data_1["hits"] == 0
        assert metrics_data_1["hit_ratio"] == 0.0

        # Second fetch: cache hit
        get_res_2 = await ac.get(f"/users/{user_id}", headers=admin_auth_headers)
        assert get_res_2.status_code == 200

        metrics_res_2 = await ac.get("/metrics/cache")
        assert metrics_res_2.status_code == 200
        metrics_data_2 = metrics_res_2.json()
        assert metrics_data_2["misses"] == 1
        assert metrics_data_2["hits"] == 1
        assert metrics_data_2["hit_ratio"] == 0.5
