"""Integration and unit tests for Day 33: Advanced Caching Architectures (Write-Through & Write-Behind)."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.repositories.user_repository import InMemoryUserRepository, UserEntity
from app.schemas.user import UserCreate, UserUpdate
from app.services.analytics_service import (
    REDIS_DIRTY_VIEWS_KEY,
    REDIS_PENDING_VIEWS_KEY,
    AnalyticsService,
)
from app.services.cache_service import CacheService
from app.services.user_service import (
    CACHE_USER_PREFIX,
    CACHE_USER_TTL_SECONDS,
    UserService,
)


@pytest.mark.asyncio
async def test_write_through_updates_db_and_warms_cache(fake_redis: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test 1: Write-Through synchronously writes to DB repository AND warms Redis cache.

    Subsequent read must immediately hit cache without invoking repo.get_by_id.
    """
    repo = InMemoryUserRepository()
    cache_service = CacheService(redis_client=fake_redis)
    await cache_service.reset_metrics()
    user_service = UserService(repository=repo, cache_service=cache_service)

    # 1. Register a user
    user = await user_service.register_user(
        UserCreate(
            email="writethrough@example.com",
            username="writethrough_orig",
            password="SecurePassword123!",
            password_confirm="SecurePassword123!",
            age=26,
        )
    )
    user_id = user.id
    cache_key = f"{CACHE_USER_PREFIX}{user_id}"

    # 2. Perform Write-Through update
    update_payload = UserUpdate(
        username="writethrough_updated",
        email="updated_email@example.com",
        age=27,
    )
    updated_user = await user_service.update_user_write_through(user_id=user_id, payload=update_payload)

    # Assert entity returned has updated fields
    assert updated_user.username == "writethrough_updated"
    assert updated_user.email == "updated_email@example.com"
    assert updated_user.age == 27

    # Assert underlying repository has updated data
    db_entity = await repo.get_by_id(user_id)
    assert db_entity is not None
    assert db_entity.username == "writethrough_updated"

    # Assert Redis cache was immediately populated with TTL <= 300s
    assert await fake_redis.exists(cache_key) == 1
    ttl = await fake_redis.ttl(cache_key)
    assert 0 < ttl <= CACHE_USER_TTL_SECONDS

    # 3. Intercept repository get_by_id to assert it is NOT invoked on subsequent read
    called_db = False

    async def spy_get_by_id(uid: int) -> UserEntity | None:
        nonlocal called_db
        called_db = True
        return await InMemoryUserRepository.get_by_id(repo, uid)

    monkeypatch.setattr(repo, "get_by_id", spy_get_by_id)

    # 4. Immediate read should hit cache directly (0 cache misses, 1 hit)
    read_user = await user_service.get_user_by_id(user_id)
    assert read_user.username == "writethrough_updated"
    assert called_db is False

    metrics = await cache_service.get_metrics()
    assert metrics["hits"] == 1
    assert metrics["misses"] == 0


@pytest.mark.asyncio
async def test_write_behind_view_ingestion_latency_and_buffering(
    fake_redis: Any,
) -> None:
    """Test 2: Write-Behind view counter ingestion handles 50 concurrent hits sub-millisecond.

    Redis pending hash holds 50, dirty set holds user_id, DB views remain 0.
    """
    repo = InMemoryUserRepository()
    analytics_service = AnalyticsService(redis_client=fake_redis, repository=repo)

    user_id = 42

    # Ingest 50 concurrent views
    tasks = [analytics_service.record_view(user_id) for _ in range(50)]
    results = await asyncio.gather(*tasks)

    assert len(results) == 50
    for res in results:
        assert res["user_id"] == user_id
        assert res["status"] == "recorded"
        assert res["mode"] == "write-behind"

    # Verify Redis pending view count is exactly 50
    pending_str = await fake_redis.hget(REDIS_PENDING_VIEWS_KEY, str(user_id))
    assert pending_str == "50"

    # Verify user_id is recorded in dirty set
    is_member = await fake_redis.sismember(REDIS_DIRTY_VIEWS_KEY, str(user_id))
    assert is_member == 1

    # Verify database repository views remain 0 (no blocking DB writes)
    db_views = await repo.get_views(user_id)
    assert db_views == 0

    # Summary should report 0 persistent, 50 pending, 50 total
    summary = await analytics_service.get_user_views(user_id)
    assert summary["persistent_views"] == 0
    assert summary["pending_views"] == 50
    assert summary["total_views"] == 50


@pytest.mark.asyncio
async def test_write_behind_batch_flusher(
    fake_redis: Any,
) -> None:
    """Test 3: Batch flusher synchronizes accumulated views into DB and clears Redis buffer."""
    repo = InMemoryUserRepository()
    analytics_service = AnalyticsService(redis_client=fake_redis, repository=repo)

    # Ingest views for two users
    for _ in range(30):
        await analytics_service.record_view(user_id=101)
    for _ in range(20):
        await analytics_service.record_view(user_id=102)

    assert await repo.get_views(101) == 0
    assert await repo.get_views(102) == 0

    # Trigger flush
    flush_result = await analytics_service.sync_pending_views_to_db()

    assert flush_result["flushed_records"] == 2
    assert flush_result["total_views"] == 50

    # Verify DB now contains the flushed views
    assert await repo.get_views(101) == 30
    assert await repo.get_views(102) == 20

    # Verify Redis pending hash and dirty set are cleared
    assert await fake_redis.exists(REDIS_PENDING_VIEWS_KEY) == 0
    assert await fake_redis.exists(REDIS_DIRTY_VIEWS_KEY) == 0

    # Check summaries
    summary_101 = await analytics_service.get_user_views(101)
    assert summary_101["persistent_views"] == 30
    assert summary_101["pending_views"] == 0
    assert summary_101["total_views"] == 30


@pytest.mark.asyncio
async def test_atomic_flush_concurrency_zero_lost_updates(
    fake_redis: Any,
) -> None:
    """Test 4: Atomic pipeline flush guarantees zero lost updates during concurrent ingestion.

    Interleaving view increments while flush is executing must strictly conserve total count.
    """
    repo = InMemoryUserRepository()
    analytics_service = AnalyticsService(redis_client=fake_redis, repository=repo)
    user_id = 200

    # 1. Initial 20 views
    for _ in range(20):
        await analytics_service.record_view(user_id)

    # 2. Run a batch flush concurrently with 30 new incoming views
    async def ingest_burst() -> None:
        for _ in range(30):
            await analytics_service.record_view(user_id)

    await asyncio.gather(
        analytics_service.sync_pending_views_to_db(),
        ingest_burst(),
    )

    # 3. Second flush to drain any views that arrived in the new bucket
    await analytics_service.sync_pending_views_to_db()

    # Assert final database view count is strictly 50 (zero lost updates)
    final_db_views = await repo.get_views(user_id)
    assert final_db_views == 50

    summary = await analytics_service.get_user_views(user_id)
    assert summary["persistent_views"] == 50
    assert summary["pending_views"] == 0
    assert summary["total_views"] == 50


@pytest.mark.asyncio
async def test_http_write_through_and_write_behind_endpoints(
    fake_redis: Any,
    admin_user: dict[str, Any],
    admin_auth_headers: dict[str, str],
) -> None:
    """Test 5: Full HTTP integration tests for PUT /write-through, POST /view, GET /views, and POST /metrics/views/flush."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        # Step A: Register user
        reg_res = await ac.post(
            "/users/",
            json={
                "email": "http_wb_user@example.com",
                "username": "http_wb_user",
                "password": "SecurePassword123!",
                "password_confirm": "SecurePassword123!",
                "age": 28,
                "role": "user",
            },
        )
        assert reg_res.status_code == 201
        user_id = reg_res.json()["id"]

        # Step B: Write-Through update via HTTP PUT
        update_res = await ac.put(
            f"/users/{user_id}/write-through",
            headers=admin_auth_headers,
            json={
                "username": "http_wb_updated",
                "email": "http_wb_updated@example.com",
                "age": 29,
            },
        )
        assert update_res.status_code == 200
        assert update_res.json()["username"] == "http_wb_updated"

        # Verify Redis key is populated
        cache_key = f"{CACHE_USER_PREFIX}{user_id}"
        assert await fake_redis.exists(cache_key) == 1

        # Step C: Record 5 profile views via HTTP POST (Status 202 Accepted)
        for _ in range(5):
            view_res = await ac.post(f"/users/{user_id}/view")
            assert view_res.status_code == 202
            assert view_res.json()["user_id"] == user_id
            assert view_res.json()["status"] == "recorded"
            assert view_res.json()["mode"] == "write-behind"

        # Step D: Check views summary via HTTP GET
        summary_res = await ac.get(f"/users/{user_id}/views")
        assert summary_res.status_code == 200
        summary_data = summary_res.json()
        assert summary_data["user_id"] == user_id
        assert summary_data["persistent_views"] == 0
        assert summary_data["pending_views"] == 5
        assert summary_data["total_views"] == 5

        # Step E: Trigger batch flush via HTTP POST /metrics/views/flush
        flush_res = await ac.post("/metrics/views/flush")
        assert flush_res.status_code == 200
        flush_data = flush_res.json()
        assert flush_data["flushed_records"] >= 1
        assert flush_data["total_views"] >= 5

        # Step F: Re-check views summary (pending should now be 0, persistent should be 5)
        summary_res_after = await ac.get(f"/users/{user_id}/views")
        assert summary_res_after.status_code == 200
        after_data = summary_res_after.json()
        assert after_data["persistent_views"] == 5
        assert after_data["pending_views"] == 0
        assert after_data["total_views"] == 5
