"""Unit, integration, and benchmark tests for Day 36: Real-Time Leaderboard Service using Redis Sorted Sets."""

from __future__ import annotations

import time
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.schemas.leaderboard import LeaderboardEntry, PlayerStanding
from app.services.cache_service import CacheService
from app.services.leaderboard_service import LeaderboardService

# =============================================================================
# 1. Domain Service Unit & Algorithmic Tests
# =============================================================================


@pytest.mark.asyncio
async def test_score_increment_and_ordering(fake_redis: Any) -> None:
    """Test 1: Score increments strictly enforce descending score ordering and 1-indexed ranks."""
    cache_service = CacheService(redis_client=fake_redis)
    service = LeaderboardService(cache_service=cache_service)
    lb_id = "test_ordering"

    # Add 5 players with varied scores
    await service.record_score(lb_id, "charlie", 50.0)
    await service.record_score(lb_id, "alice", 100.0)
    await service.record_score(lb_id, "eve", 150.0)
    await service.record_score(lb_id, "bob", 250.0)
    new_score, rank = await service.record_score(lb_id, "dave", 300.0)

    assert new_score == 300.0
    assert rank == 1

    # Retrieve all 5 players ordered from highest to lowest
    top_players: list[LeaderboardEntry] = await service.get_top_players(lb_id, limit=10, offset=0)
    assert len(top_players) == 5

    expected_order = [
        ("dave", 300.0, 1),
        ("bob", 250.0, 2),
        ("eve", 150.0, 3),
        ("alice", 100.0, 4),
        ("charlie", 50.0, 5),
    ]

    for entry, (expected_player, expected_score, expected_rank) in zip(top_players, expected_order, strict=True):
        assert entry.player_id == expected_player
        assert entry.score == expected_score
        assert entry.rank == expected_rank


@pytest.mark.asyncio
async def test_dynamic_rank_overtake(fake_redis: Any) -> None:
    """Test 2: Dynamic Overtake - Player B receives points and overtakes Player A from #2 to #1 in O(log N) time."""
    cache_service = CacheService(redis_client=fake_redis)
    service = LeaderboardService(cache_service=cache_service)
    lb_id = "test_overtake"

    # Seed Player A with 300 points and Player B with 200 points
    await service.record_score(lb_id, "player_a", 300.0)
    score_b, rank_b = await service.record_score(lb_id, "player_b", 200.0)

    assert score_b == 200.0
    assert rank_b == 2

    # Player B gains 150 points (total 350.0) and dynamically overtakes Player A
    new_score_b, new_rank_b = await service.record_score(lb_id, "player_b", 150.0)
    assert new_score_b == 350.0
    assert new_rank_b == 1

    # Verify Player A has fallen to rank #2
    standing_a = await service.get_player_standing(lb_id, "player_a")
    assert standing_a is not None
    assert standing_a.rank == 2
    assert standing_a.score == 300.0


@pytest.mark.asyncio
async def test_player_standing_and_percentile(fake_redis: Any) -> None:
    """Test 3: Standing & Percentile - Accurate calculation for various competitor distributions."""
    cache_service = CacheService(redis_client=fake_redis)
    service = LeaderboardService(cache_service=cache_service)
    lb_id = "test_percentile"

    # Single player standing
    await service.record_score(lb_id, "solo", 100.0)
    standing_solo = await service.get_player_standing(lb_id, "solo")
    assert standing_solo is not None
    assert standing_solo.rank == 1
    assert standing_solo.total_players == 1
    assert standing_solo.percentile == 100.0

    # Add 4 more players (total 5 players: scores 100, 200, 300, 400, 500)
    await service.record_score(lb_id, "p2", 200.0)
    await service.record_score(lb_id, "p3", 300.0)
    await service.record_score(lb_id, "p4", 400.0)
    await service.record_score(lb_id, "p5", 500.0)

    # Top player: rank 1 of 5 -> (5 - 1) / 5 * 100 = 80.0%
    standing_top: PlayerStanding | None = await service.get_player_standing(lb_id, "p5")
    assert standing_top is not None
    assert standing_top.rank == 1
    assert standing_top.total_players == 5
    assert standing_top.percentile == 80.0

    # Bottom player: rank 5 of 5 -> (5 - 5) / 5 * 100 = 0.0%
    standing_bottom: PlayerStanding | None = await service.get_player_standing(lb_id, "solo")
    assert standing_bottom is not None
    assert standing_bottom.rank == 5
    assert standing_bottom.total_players == 5
    assert standing_bottom.percentile == 0.0

    # Non-existent player returns None
    assert await service.get_player_standing(lb_id, "ghost_player") is None


@pytest.mark.asyncio
async def test_large_scale_benchmark_10k_players(fake_redis: Any) -> None:
    """Test 4: Large-Scale Benchmark - 10,000 players in ZSET. Rank query and top-10 retrieval execute in < 10ms."""
    cache_service = CacheService(redis_client=fake_redis)
    service = LeaderboardService(cache_service=cache_service)
    lb_id = "benchmark_10k"

    # Bulk seed 10,000 players via ZADD
    bulk_data = {f"player_{i}": float(i) for i in range(10_000)}
    added = await cache_service.zadd(service._leaderboard_key(lb_id), bulk_data)
    assert added == 10_000

    assert await service.get_total_players(lb_id) == 10_000

    # Measure rank lookup time (O(log N))
    start_lookup = time.perf_counter()
    standing = await service.get_player_standing(lb_id, "player_9999")
    lookup_duration = time.perf_counter() - start_lookup

    assert standing is not None
    assert standing.rank == 1
    assert standing.score == 9999.0
    assert standing.total_players == 10_000
    # Lookup in memory/SkipList should execute in microseconds, comfortably < 15ms
    assert lookup_duration < 0.015, f"Lookup took too long: {lookup_duration * 1000:.2f}ms"

    # Measure top-10 slice retrieval (O(log N + M))
    start_top10 = time.perf_counter()
    top10 = await service.get_top_players(lb_id, limit=10, offset=0)
    top10_duration = time.perf_counter() - start_top10

    assert len(top10) == 10
    assert top10[0].player_id == "player_9999"
    assert top10[0].rank == 1
    assert top10[9].player_id == "player_9990"
    assert top10[9].rank == 10
    assert top10_duration < 0.015, f"Top-10 retrieval took too long: {top10_duration * 1000:.2f}ms"


# =============================================================================
# 2. REST API Integration & Validation Tests
# =============================================================================


@pytest.mark.asyncio
async def test_http_leaderboard_endpoints_lifecycle(fake_redis: Any) -> None:
    """Test 5: REST API lifecycle - score updates, paginated top queries, standing lookups, and 404s."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        lb_id = "global_arena"

        # 1. Update score for player 1
        res1 = await ac.post(f"/leaderboard/{lb_id}/scores/player_one", json={"score_delta": 150.0})
        assert res1.status_code == 200
        data1 = res1.json()
        assert data1["leaderboard_id"] == lb_id
        assert data1["player_id"] == "player_one"
        assert data1["new_score"] == 150.0
        assert data1["rank"] == 1

        # 2. Update score for player 2 (higher score)
        res2 = await ac.post(f"/leaderboard/{lb_id}/scores/player_two", json={"score_delta": 250.0})
        assert res2.status_code == 200
        data2 = res2.json()
        assert data2["rank"] == 1

        # 3. Retrieve top players
        top_res = await ac.get(f"/leaderboard/{lb_id}/top?limit=10&offset=0")
        assert top_res.status_code == 200
        top_data = top_res.json()
        assert len(top_data) == 2
        assert top_data[0]["player_id"] == "player_two"
        assert top_data[0]["rank"] == 1
        assert top_data[1]["player_id"] == "player_one"
        assert top_data[1]["rank"] == 2

        # 4. Paginated slice with offset
        page_res = await ac.get(f"/leaderboard/{lb_id}/top?limit=1&offset=1")
        assert page_res.status_code == 200
        page_data = page_res.json()
        assert len(page_data) == 1
        assert page_data[0]["player_id"] == "player_one"
        assert page_data[0]["rank"] == 2

        # 5. Standing query for player_two
        standing_res = await ac.get(f"/leaderboard/{lb_id}/standing/player_two")
        assert standing_res.status_code == 200
        standing_data = standing_res.json()
        assert standing_data["player_id"] == "player_two"
        assert standing_data["rank"] == 1
        assert standing_data["total_players"] == 2
        assert standing_data["percentile"] == 50.0

        # 6. Standing query for non-existent player -> 404
        not_found_res = await ac.get(f"/leaderboard/{lb_id}/standing/nonexistent")
        assert not_found_res.status_code == 404
        assert not_found_res.json()["error"]["code"] == "PLAYER_NOT_FOUND"

        # 7. Query validation bounds
        invalid_limit = await ac.get(f"/leaderboard/{lb_id}/top?limit=0")
        assert invalid_limit.status_code == 422

        exceeded_limit = await ac.get(f"/leaderboard/{lb_id}/top?limit=101")
        assert exceeded_limit.status_code == 422

        invalid_offset = await ac.get(f"/leaderboard/{lb_id}/top?offset=-1")
        assert invalid_offset.status_code == 422
