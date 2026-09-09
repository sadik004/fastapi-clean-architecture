"""Tests for Redis Async Basics: Connection Pooling, Strings with TTL, Hashes, Lists, and Health Check."""

import asyncio
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.core.redis import get_redis_pool_status
from app.services.cache_service import CacheService


@pytest.mark.asyncio
async def test_redis_strings_set_get_and_none(fake_redis: Any) -> None:
    """Verify set_str and get_str for existing and non-existing keys."""
    service = CacheService(redis_client=fake_redis)

    assert await service.get_str("nonexistent_key") is None

    success = await service.set_str("user:100:name", "Alice")
    assert success is True

    val = await service.get_str("user:100:name")
    assert val == "Alice"


@pytest.mark.asyncio
async def test_redis_strings_ttl_expiration(fake_redis: Any) -> None:
    """Verify that keys created with TTL expire after the specified duration."""
    service = CacheService(redis_client=fake_redis)

    # Store OTP token with 1 second TTL
    await service.set_str("auth:otp:user_42", "948271", expire_seconds=1)
    assert await service.get_str("auth:otp:user_42") == "948271"

    # Wait for TTL expiration
    await asyncio.sleep(1.1)
    assert await service.get_str("auth:otp:user_42") is None


@pytest.mark.asyncio
async def test_redis_strings_atomic_increment(fake_redis: Any) -> None:
    """Verify atomic counter increment operations."""
    service = CacheService(redis_client=fake_redis)

    # Increment from zero
    count1 = await service.increment("metrics:page_views")
    assert count1 == 1

    # Increment by custom amount
    count2 = await service.increment("metrics:page_views", amount=5)
    assert count2 == 6

    # Verify via get_str
    val = await service.get_str("metrics:page_views")
    assert val == "6"


@pytest.mark.asyncio
async def test_redis_hashes_set_and_get(fake_redis: Any) -> None:
    """Verify Hash data structure operations for storing and retrieving dictionary objects."""
    service = CacheService(redis_client=fake_redis)

    user_profile = {
        "username": "coder_pro",
        "email": "coder@example.com",
        "role": "engineer",
    }

    # Set hash dictionary
    await service.hset_dict("user:profile:1", user_profile)

    # Get single field
    email = await service.hget_field("user:profile:1", "email")
    assert email == "coder@example.com"

    # Get nonexistent field
    missing = await service.hget_field("user:profile:1", "bio")
    assert missing is None

    # Get entire hash dictionary
    retrieved = await service.hget_dict("user:profile:1")
    assert retrieved == user_profile


@pytest.mark.asyncio
async def test_redis_lists_queue_push_pop_and_range(fake_redis: Any) -> None:
    """Verify List data structure operations for FIFO queue push, pop, and range retrieval."""
    service = CacheService(redis_client=fake_redis)

    # Push items to head: item1, then item2, then item3
    # List state: ["task_c", "task_b", "task_a"]
    await service.lpush_item("job:queue", "task_a")
    await service.lpush_item("job:queue", "task_b")
    await service.lpush_item("job:queue", "task_c")

    # Range inspection
    items = await service.lrange_items("job:queue", 0, -1)
    assert items == ["task_c", "task_b", "task_a"]

    # FIFO queue pop from tail (RPOP)
    popped1 = await service.rpop_item("job:queue")
    assert popped1 == "task_a"

    popped2 = await service.rpop_item("job:queue")
    assert popped2 == "task_b"

    popped3 = await service.rpop_item("job:queue")
    assert popped3 == "task_c"

    # Pop on empty list returns None
    assert await service.rpop_item("job:queue") is None


@pytest.mark.asyncio
async def test_redis_cache_service_utility_delete_and_exists(fake_redis: Any) -> None:
    """Verify delete and exists helper operations in CacheService."""
    service = CacheService(redis_client=fake_redis)

    await service.set_str("temp_key_1", "val1")
    await service.set_str("temp_key_2", "val2")

    assert await service.exists("temp_key_1") is True
    assert await service.exists("temp_key_3") is False

    deleted = await service.delete("temp_key_1", "temp_key_2")
    assert deleted == 2
    assert await service.exists("temp_key_1") is False


def test_redis_health_endpoint_success(client: TestClient, fake_redis: Any) -> None:
    """Verify GET /health/redis returns HTTP 200 with connection and pool status."""
    response = client.get("/health/redis")
    assert response.status_code == 200

    data = response.json()
    assert data["status"] == "healthy"
    assert data["redis"] == "connected"
    assert isinstance(data["ping_ms"], int | float)
    assert data["ping_ms"] >= 0.0
    assert "pool" in data
    assert data["pool"]["status"] == "active"


def test_redis_pool_status_telemetry(fake_redis: Any) -> None:
    """Verify Redis connection pool status telemetry structure."""
    status = get_redis_pool_status()
    assert status["status"] == "active"
    assert "max_connections" in status
    assert status["max_connections"] == 20
