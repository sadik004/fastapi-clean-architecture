"""Unit and integration test suite for Day 35: Bloom Filter Architecture & Cache Penetration Defense."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from app.core.dsa.bloom_filter import BloomFilter
from app.core.exceptions import UserNotFoundException
from app.repositories.user_repository import InMemoryUserRepository
from app.schemas.user import UserCreate
from app.services.cache_service import CacheService
from app.services.user_service import UserService

# =============================================================================
# 1. Pure Data Structure Tests (Zero False Negatives, Bounded False Positives, Slots)
# =============================================================================


def test_bloom_filter_zero_false_negatives() -> None:
    """Test 1: Zero False Negatives Invariant.

    Inserting 1,000 items must yield 100% contains(x) is True.
    A Bloom Filter must never report that an inserted element is missing.
    """
    bf = BloomFilter(capacity=1_000, false_positive_rate=0.01)
    items = [f"user_{i}" for i in range(1_000)]

    for item in items:
        bf.add(item)

    assert bf.count == 1_000

    # Every single inserted item must be found
    for item in items:
        assert bf.contains(item) is True
        assert item in bf


def test_bloom_filter_bounded_false_positives() -> None:
    """Test 2: Bounded False Positives Invariant.

    Querying 10,000 non-existent items must yield a false positive rate <= 1.5%
    when calibrated for 1.0% target probability.
    """
    capacity = 5_000
    bf = BloomFilter(capacity=capacity, false_positive_rate=0.01)

    # Insert 5,000 known items
    for i in range(capacity):
        bf.add(f"known_user_{i}")

    # Query 10,000 unknown items
    unknown_samples = 10_000
    false_positives = 0
    for i in range(capacity, capacity + unknown_samples):
        if bf.contains(f"unknown_user_{i}"):
            false_positives += 1

    actual_fp_rate = false_positives / unknown_samples
    assert actual_fp_rate <= 0.015, f"False positive rate {actual_fp_rate:.4f} exceeded upper bound 0.015"


def test_bloom_filter_slotted_memory_footprint() -> None:
    """Test 3: Slotted Memory Footprint Validation.

    For capacity=100,000 at P=0.01, bytearray memory must be strictly < 1.5MB.
    For capacity=1,000,000 at P=0.01, bytearray memory must also be < 1.5MB (~1.14MB).
    """
    bf_100k = BloomFilter(capacity=100_000, false_positive_rate=0.01)
    assert bf_100k.size_kb < 1500.0, f"100k size {bf_100k.size_kb} KB exceeds 1.5MB"
    # Exact expectation: ~119.8 KB
    assert bf_100k.size_kb < 200.0

    bf_1m = BloomFilter(capacity=1_000_000, false_positive_rate=0.01)
    assert bf_1m.size_kb < 1500.0, f"1M size {bf_1m.size_kb} KB exceeds 1.5MB"
    # Exact expectation: ~1170 KB (~1.14MB)
    assert bf_1m.size_kb < 1300.0

    # Ensure __dict__ does not exist due to __slots__
    assert not hasattr(bf_100k, "__dict__")


def test_bloom_filter_input_validation() -> None:
    """Test invalid parameter initialization."""
    with pytest.raises(ValueError, match="Capacity must be a positive integer"):
        BloomFilter(capacity=0)

    with pytest.raises(ValueError, match="Capacity must be a positive integer"):
        BloomFilter(capacity=-10)

    with pytest.raises(ValueError, match="False positive rate must be strictly between 0 and 1"):
        BloomFilter(capacity=100, false_positive_rate=0.0)

    with pytest.raises(ValueError, match="False positive rate must be strictly between 0 and 1"):
        BloomFilter(capacity=100, false_positive_rate=1.0)


# =============================================================================
# 2. Service-Level Cache Penetration Shield Tests
# =============================================================================


@pytest.mark.asyncio
async def test_cache_penetration_shield_zero_db_and_cache_queries() -> None:
    """Test 4: Cache Penetration Defense.

    When querying 100 non-existent user IDs, the Bloom filter must block all 100 requests.
    Zero repository database queries and zero Redis cache lookups must be performed.
    """
    mock_repo = AsyncMock(spec=InMemoryUserRepository)
    mock_cache = AsyncMock(spec=CacheService)
    bf = BloomFilter(capacity=1_000, false_positive_rate=0.01)

    # Insert one valid user
    bf.add(42)

    user_service = UserService(
        repository=mock_repo,
        cache_service=mock_cache,
        bloom_filter=bf,
    )

    # Query 100 fake, non-existent user IDs
    rejected_count = 0
    for fake_id in range(1000, 1100):
        with pytest.raises(UserNotFoundException):
            await user_service.get_user_by_id(fake_id)
        rejected_count += 1

    assert rejected_count == 100
    # Strict verification: Neither Redis nor DB was queried for fake IDs
    mock_repo.get_by_id.assert_not_awaited()
    mock_cache.get_str.assert_not_awaited()


@pytest.mark.asyncio
async def test_user_registration_seeds_bloom_filter() -> None:
    """Test 5: Registering a user automatically inserts their ID into the Bloom filter."""
    repo = InMemoryUserRepository()
    bf = BloomFilter(capacity=100, false_positive_rate=0.01)
    user_service = UserService(repository=repo, bloom_filter=bf)

    assert bf.count == 0

    user = await user_service.register_user(
        UserCreate(
            email="bloom_test@example.com",
            username="bloom_user",
            password="SecurePassword123!",
            password_confirm="SecurePassword123!",
            age=25,
        )
    )

    assert bf.count == 1
    assert bf.contains(user.id) is True
    assert user.id in bf


# =============================================================================
# 3. HTTP Endpoints & Observability Tests
# =============================================================================


def test_http_bloom_filter_telemetry_endpoint(client: TestClient) -> None:
    """Test 6: GET /metrics/bloom-filter telemetry report."""
    response = client.get("/metrics/bloom-filter")
    assert response.status_code == 200
    data = response.json()

    assert "capacity" in data
    assert "bit_size" in data
    assert "bit_size_kb" in data
    assert "hash_count" in data
    assert "item_count" in data
    assert "false_positive_probability" in data
    assert data["capacity"] == 100_000
    assert data["hash_count"] == 7


def test_http_bloom_filter_check_endpoint(
    client: TestClient,
    admin_user: dict[str, Any],
) -> None:
    """Test 7: GET /metrics/bloom-filter/check/{user_id} probing."""
    admin_id = admin_user["id"]

    # 1. Existing user should probably exist (True)
    resp_existing = client.get(f"/metrics/bloom-filter/check/{admin_id}")
    assert resp_existing.status_code == 200
    data_existing = resp_existing.json()
    assert data_existing["user_id"] == admin_id
    assert data_existing["probably_exists"] is True
    assert data_existing["db_queried"] is False
    assert data_existing["status"] == "PASSED_FILTER_PROCEED_TO_CACHE"

    # 2. Fake non-existent user should definitely not exist (False)
    fake_id = 999_999
    resp_fake = client.get(f"/metrics/bloom-filter/check/{fake_id}")
    assert resp_fake.status_code == 200
    data_fake = resp_fake.json()
    assert data_fake["user_id"] == fake_id
    assert data_fake["probably_exists"] is False
    assert data_fake["db_queried"] is False
    assert data_fake["status"] == "BLOCKED_BY_FILTER_ZERO_DB_QUERY"


def test_http_user_lookup_blocked_by_bloom_filter(client: TestClient) -> None:
    """Test 8: GET /users/{id} for non-existent ID returns 404 immediately via Bloom filter."""
    non_existent_id = 888_888
    response = client.get(f"/users/{non_existent_id}")
    assert response.status_code == 404
    body = response.json()
    assert "detail" in body
