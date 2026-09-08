"""Tests for Day 26: Binary Search (O(log N)) and Two-Pointer Range Filtering Architecture."""

import time
from typing import Any

import pytest
from starlette.testclient import TestClient

from app.core.dsa.search_algorithms import (
    binary_search_bounds,
    binary_search_range,
    two_pointer_pair_search,
)
from app.main import app

# ---------------------------------------------------------------------------
# 1. Unit Tests: Boundary Correctness
# ---------------------------------------------------------------------------


def test_binary_search_bounds_empty_list() -> None:
    """Empty sequence returns (0, 0)."""
    bounds = binary_search_bounds([], min_val=10.0, max_val=20.0, key_func=lambda x: x)
    assert bounds == (0, 0)
    assert binary_search_range([], min_val=10.0, max_val=20.0, key_func=lambda x: x) == []


def test_binary_search_bounds_inverted_range() -> None:
    """min_val > max_val returns (0, 0) with zero iterations."""
    items = [10.0, 20.0, 30.0, 40.0]
    bounds = binary_search_bounds(items, min_val=35.0, max_val=25.0, key_func=lambda x: x)
    assert bounds == (0, 0)
    assert binary_search_range(items, min_val=35.0, max_val=25.0, key_func=lambda x: x) == []


def test_binary_search_bounds_out_of_range_lower_and_upper() -> None:
    """Queries completely outside the data range return empty slices."""
    items = [10.0, 20.0, 30.0, 40.0, 50.0]

    # Strictly below minimum
    low_bounds = binary_search_bounds(items, min_val=1.0, max_val=5.0, key_func=lambda x: x)
    assert low_bounds[0] == low_bounds[1] == 0
    assert binary_search_range(items, min_val=1.0, max_val=5.0, key_func=lambda x: x) == []

    # Strictly above maximum
    high_bounds = binary_search_bounds(items, min_val=60.0, max_val=100.0, key_func=lambda x: x)
    assert high_bounds[0] == high_bounds[1] == len(items)
    assert binary_search_range(items, min_val=60.0, max_val=100.0, key_func=lambda x: x) == []


def test_binary_search_bounds_exact_matches_and_duplicates() -> None:
    """Accurately finds ranges including duplicates and single-value exact matches."""
    # Duplicates in sorted order
    items = [10.0, 20.0, 20.0, 20.0, 30.0, 40.0]

    # Exact match for duplicate block [20.0, 20.0, 20.0]
    left, right = binary_search_bounds(items, min_val=20.0, max_val=20.0, key_func=lambda x: x)
    assert (left, right) == (1, 4)
    assert binary_search_range(items, min_val=20.0, max_val=20.0, key_func=lambda x: x) == [20.0, 20.0, 20.0]

    # Range covering boundaries
    res = binary_search_range(items, min_val=15.0, max_val=35.0, key_func=lambda x: x)
    assert res == [20.0, 20.0, 20.0, 30.0]

    # Range spanning entire list
    full_res = binary_search_range(items, min_val=0.0, max_val=100.0, key_func=lambda x: x)
    assert full_res == items


def test_binary_search_custom_objects() -> None:
    """Binary search bounds works seamlessly over custom dataclasses/dictionaries."""
    records: list[dict[str, Any]] = [
        {"id": 1, "age": 18},
        {"id": 2, "age": 25},
        {"id": 3, "age": 25},
        {"id": 4, "age": 30},
        {"id": 5, "age": 42},
    ]

    matched = binary_search_range(
        sorted_items=records,
        min_val=20.0,
        max_val=35.0,
        key_func=lambda r: float(int(r["age"])),
    )
    assert [r["id"] for r in matched] == [2, 3, 4]


# ---------------------------------------------------------------------------
# 2. Benchmark: Logarithmic Scaling on 100,000 Elements
# ---------------------------------------------------------------------------


def test_binary_search_logarithmic_speed_100k_benchmark() -> None:
    """Assert binary search range bounds executes in under 0.2ms over 100,000 elements.

    Demonstrates the mathematical efficiency of O(log N) where log2(100,000) ~ 17
    comparisons versus 100,000 linear iterations.
    """
    n = 100_000
    sorted_dataset = [float(i * 2) for i in range(n)]  # [0.0, 2.0, 4.0, ..., 199998.0]

    min_query = 50_000.0
    max_query = 50_100.0

    # Warm-up run
    _ = binary_search_bounds(sorted_dataset, min_query, max_query, key_func=lambda x: x)

    # Benchmark run
    start_ns = time.perf_counter_ns()
    left_idx, right_idx = binary_search_bounds(
        sorted_dataset,
        min_query,
        max_query,
        key_func=lambda x: x,
    )
    elapsed_ms = (time.perf_counter_ns() - start_ns) / 1_000_000.0

    # Verification: slice matches expected count (50_000 to 50_100 inclusive is 51 even numbers)
    assert left_idx == 25_000
    assert right_idx == 25_051
    assert right_idx - left_idx == 51

    # Strict O(log N) speed verification (< 0.2ms)
    assert elapsed_ms < 0.2, f"Expected O(log N) execution < 0.2ms, got {elapsed_ms:.4f}ms"


# ---------------------------------------------------------------------------
# 3. Unit Tests: Two-Pointer Pair Search
# ---------------------------------------------------------------------------


def test_two_pointer_pair_search_basic() -> None:
    """Finds exact target pairs at extremes and middle."""
    items = [2.0, 7.0, 11.0, 15.0]

    # Extreme pair: 2.0 + 15.0 = 17.0
    pair = two_pointer_pair_search(items, target=17.0, key_func=lambda x: x)
    assert pair == (2.0, 15.0)

    # Middle pair: 7.0 + 11.0 = 18.0
    pair = two_pointer_pair_search(items, target=18.0, key_func=lambda x: x)
    assert pair == (7.0, 11.0)


def test_two_pointer_pair_search_negative_cases() -> None:
    """Returns None when no pair matches target or dataset has < 2 elements."""
    items = [1.0, 3.0, 5.0, 8.0]

    # Target impossible
    assert two_pointer_pair_search(items, target=100.0, key_func=lambda x: x) is None
    assert two_pointer_pair_search(items, target=2.0, key_func=lambda x: x) is None

    # Fewer than 2 elements
    assert two_pointer_pair_search([42.0], target=42.0, key_func=lambda x: x) is None
    assert two_pointer_pair_search([], target=10.0, key_func=lambda x: x) is None


def test_two_pointer_pair_search_with_objects() -> None:
    """Verifies two-pointer search on custom structured records."""
    users: list[dict[str, Any]] = [
        {"username": "alice", "score": 10},
        {"username": "bob", "score": 25},
        {"username": "carol", "score": 40},
        {"username": "dave", "score": 60},
    ]

    # target sum = 65 (bob: 25 + carol: 40)
    match = two_pointer_pair_search(users, target=65.0, key_func=lambda u: float(int(u["score"])))
    assert match is not None
    assert match[0]["username"] == "bob"
    assert match[1]["username"] == "carol"


# ---------------------------------------------------------------------------
# 4. Integration Tests: GET /users/filter/by-age Endpoint
# ---------------------------------------------------------------------------


@pytest.fixture
def client() -> TestClient:
    """Fixture providing Starlette TestClient instance."""
    return TestClient(app)


def test_filter_users_by_age_success(client: TestClient) -> None:
    """GET /users/filter/by-age filters registered users within age boundaries."""
    # Register test users with varying ages
    test_users = [
        {
            "email": "age19@example.com",
            "username": "young_coder",
            "password": "Password123!",
            "password_confirm": "Password123!",
            "age": 19,
        },
        {
            "email": "age28@example.com",
            "username": "mid_engineer",
            "password": "Password123!",
            "password_confirm": "Password123!",
            "age": 28,
        },
        {
            "email": "age35@example.com",
            "username": "lead_architect",
            "password": "Password123!",
            "password_confirm": "Password123!",
            "age": 35,
        },
        {
            "email": "age55@example.com",
            "username": "senior_fellow",
            "password": "Password123!",
            "password_confirm": "Password123!",
            "age": 55,
        },
    ]

    for u in test_users:
        # Register if not already present
        res = client.post("/users/", json=u)
        assert res.status_code in (201, 409)

    # Filter for age range 25 to 40
    res = client.get("/users/filter/by-age?min_age=25&max_age=40")
    assert res.status_code == 200
    data = res.json()
    assert isinstance(data, list)

    usernames = [user["username"] for user in data]
    assert "mid_engineer" in usernames
    assert "lead_architect" in usernames
    assert "young_coder" not in usernames
    assert "senior_fellow" not in usernames

    # Verify all returned users satisfy 25 <= age <= 40
    for user in data:
        assert user["age"] is not None
        assert 25 <= user["age"] <= 40


def test_filter_users_by_age_inverted_bounds(client: TestClient) -> None:
    """GET /users/filter/by-age?min_age=50&max_age=20 returns HTTP 400 Bad Request."""
    res = client.get("/users/filter/by-age?min_age=50&max_age=20")
    assert res.status_code == 400
    assert "cannot be greater than max_age" in res.json()["detail"]


def test_filter_users_by_age_validation_error(client: TestClient) -> None:
    """GET /users/filter/by-age with out-of-range bounds triggers HTTP 422."""
    # Negative min_age
    res = client.get("/users/filter/by-age?min_age=-5")
    assert res.status_code == 422

    # max_age exceeding 150
    res = client.get("/users/filter/by-age?max_age=200")
    assert res.status_code == 422
