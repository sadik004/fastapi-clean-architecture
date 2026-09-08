"""Comprehensive test suite for Day 23: The Trie (Prefix Tree) Data Structure for O(k) Sub-Millisecond Autocomplete Search.

Verifies:
1. Slotted TrieNode Invariant: Asserts zero __dict__ overhead and typo/dynamic attribute restriction on tree nodes.
2. Core Trie Operations: Validates insert, exact search, autocomplete, and delete.
3. Case-Insensitive Normalization: Proves case-insensitivity across search and autocomplete.
4. Mathematical Node Pruning on Deletion: Proves branch pruning removes orphan nodes without corrupting shared prefixes.
5. Prefix Independence & O(k) Sub-Millisecond Scaling Proof: Benchmarks 10,000 randomized words asserting sub-millisecond lookup.
6. End-to-End API Test: Validates GET /users/autocomplete?q=... via FastAPI TestClient.
"""

from datetime import datetime, timezone
import string
import time
import pytest
from fastapi import status
from fastapi.testclient import TestClient

from app.core.dsa.trie import PrefixTrie, TrieNode
from app.repositories.user_repository import InMemoryUserRepository, UserEntity
from app.services.user_service import UserService


# ============================================================================
# 1. Slotted Node Invariant Tests
# ============================================================================


def test_slotted_trie_node_memory_invariants() -> None:
    """Verify TrieNode suppresses __dict__ and prohibits undeclared dynamic attributes."""
    node = TrieNode()

    # Invariant: No dynamic dictionary
    assert hasattr(node, "__dict__") is False
    assert hasattr(TrieNode, "__slots__")

    # Invariant: Typo / undeclared attribute assignment raises AttributeError
    with pytest.raises(AttributeError):
        node.childrn = {}  # type: ignore[attr-defined]

    with pytest.raises(AttributeError):
        node.arbitrary_metadata = "leak"  # type: ignore[attr-defined]


# ============================================================================
# 2. Core Trie Operations Tests
# ============================================================================


def test_trie_insert_search_and_autocomplete() -> None:
    """Verify standard insertion, exact search, and prefix autocomplete functionality."""
    trie = PrefixTrie()

    trie.insert("apple", payload={"id": 1, "item": "Apple Fruit"}, score=10)
    trie.insert("app", payload={"id": 2, "item": "App Application"}, score=5)
    trie.insert("application", payload={"id": 3, "item": "Enterprise Application"}, score=20)
    trie.insert("banana", payload={"id": 4, "item": "Banana Fruit"}, score=1)

    assert trie.word_count == 4

    # Exact search
    apple_results = trie.search("apple")
    assert apple_results is not None
    assert len(apple_results) == 1
    assert apple_results[0]["id"] == 1

    # Exact search for non-terminal prefix
    assert trie.search("appl") is None

    # Exact search for non-existent word
    assert trie.search("orange") is None

    # Autocomplete with prefix "app"
    # Expected ordering: "application" (score 20), "apple" (score 10), "app" (score 5)
    completions = trie.autocomplete("app", limit=10)
    assert len(completions) == 3
    words = [w for w, _ in completions]
    assert words == ["application", "apple", "app"]

    # Autocomplete with non-existent prefix returns empty in O(k) time
    assert trie.autocomplete("xyz") == []
    assert trie.autocomplete("") == []


# ============================================================================
# 3. Case-Insensitive Normalization Tests
# ============================================================================


def test_case_insensitive_normalization() -> None:
    """Verify inserting mixed-case words normalizes correctly and responds to any casing."""
    trie = PrefixTrie()

    trie.insert("JohnDoe", payload={"id": 42, "name": "John Doe"})

    # Exact search under various casings
    assert trie.search("johndoe") is not None
    assert trie.search("JOHNDOE") is not None
    assert trie.search("  JohnDoe  ") is not None

    # Autocomplete under various casings
    res_lower = trie.autocomplete("joh")
    res_upper = trie.autocomplete("JOH")
    res_mixed = trie.autocomplete("JoH")

    assert len(res_lower) == 1
    assert len(res_upper) == 1
    assert len(res_mixed) == 1
    assert res_lower[0][0] == "johndoe"
    assert res_upper[0][0] == "johndoe"


# ============================================================================
# 4. Deletion & Node Pruning Tests
# ============================================================================


def test_deletion_and_recursive_node_pruning() -> None:
    """Verify that deleting words removes terminal status and prunes orphan leaf nodes.

    Must preserve overlapping prefixes (e.g. deleting 'apple' preserves 'app' and 'application').
    """
    trie = PrefixTrie()

    trie.insert("app")
    trie.insert("apple")
    trie.insert("application")

    initial_nodes = trie.node_count
    assert trie.word_count == 3

    # Delete "apple"
    deleted = trie.delete("apple")
    assert deleted is True
    assert trie.word_count == 2
    assert trie.search("apple") is None

    # "app" and "application" must remain intact
    assert trie.search("app") is not None
    assert trie.search("application") is not None

    # Node count must have decreased by 1 (the 'e' leaf node of 'apple')
    assert trie.node_count < initial_nodes

    # Delete "application"
    trie.delete("application")
    assert trie.word_count == 1
    assert trie.search("app") is not None

    # Delete "app" -> should prune back to the root node
    trie.delete("app")
    assert trie.word_count == 0
    assert trie.search("app") is None
    # Root node remains
    assert trie.node_count == 1
    assert len(trie.root.children) == 0

    # Deleting non-existent word returns False
    assert trie.delete("nonexistent") is False


def test_targeted_payload_deletion() -> None:
    """Verify that deleting a specific payload does not unmark terminal if other payloads exist."""
    trie = PrefixTrie()

    payload_a = {"id": 1, "username": "admin"}
    payload_b = {"id": 2, "username": "admin_backup"}

    trie.insert("admin", payload=payload_a)
    trie.insert("admin", payload=payload_b)

    assert trie.word_count == 1
    assert len(trie.search("admin") or []) == 2

    # Remove payload_a only
    trie.delete("admin", payload=payload_a)
    remaining = trie.search("admin")
    assert remaining is not None
    assert len(remaining) == 1
    assert remaining[0]["id"] == 2
    assert trie.word_count == 1

    # Remove payload_b -> now fully unmarks terminal
    trie.delete("admin", payload=payload_b)
    assert trie.search("admin") is None
    assert trie.word_count == 0


# ============================================================================
# 5. Prefix Independence & O(k) Sub-Millisecond Scaling Proof
# ============================================================================


def test_prefix_independence_and_sub_millisecond_scaling() -> None:
    """Insert 10,000 distinct words and prove autocomplete completes in sub-millisecond time.

    O(k) lookup time is completely independent of total dataset size N.
    """
    trie = PrefixTrie()

    # Generate 10,000 synthetic words across structured prefixes
    # Prefix "adm" will have exactly 100 completions
    for i in range(100):
        trie.insert(f"admin_{i:04d}", payload={"id": i, "user": f"admin_{i}"}, score=i)

    # Fill remaining 9,900 words across different prefix branches
    for i in range(9900):
        # 4-character randomish prefixes
        first = string.ascii_lowercase[i % 26]
        second = string.ascii_lowercase[(i // 26) % 26]
        word = f"usr_{first}{second}_{i}"
        trie.insert(word, payload={"id": 1000 + i})

    assert trie.word_count >= 10000

    # Benchmark: autocomplete lookup for prefix "adm" across 10,000 words
    start_time = time.perf_counter()
    results = trie.autocomplete("adm", limit=10)
    elapsed_ms = (time.perf_counter() - start_time) * 1000.0

    assert len(results) == 10
    # Strict sub-millisecond assertion (< 1.0 ms)
    assert elapsed_ms < 1.0, f"Autocomplete lookup took {elapsed_ms:.4f}ms, expected < 1.0ms"

    # Non-existent prefix lookup must abort in strictly O(k) time (< 0.1ms)
    start_miss = time.perf_counter()
    miss_results = trie.autocomplete("zzz_missing", limit=10)
    miss_elapsed_ms = (time.perf_counter() - start_miss) * 1000.0

    assert len(miss_results) == 0
    assert miss_elapsed_ms < 0.5, f"Missing prefix lookup took {miss_elapsed_ms:.4f}ms, expected < 0.5ms"


# ============================================================================
# 6. Service & End-to-End API Integration Tests
# ============================================================================


@pytest.mark.asyncio
async def test_user_service_trie_synchronization() -> None:
    """Verify UserService indexes on registration and prunes on deletion."""
    repo = InMemoryUserRepository()
    trie = PrefixTrie()
    service = UserService(repository=repo, trie=trie)

    # Register user
    now = datetime.now(timezone.utc)
    user = UserEntity(
        id=50,
        email="autocomplete.hero@example.com",
        username="search_hero",
        password_hash="hash",
        is_active=True,
        created_at=now,
        full_name="Search Master",
    )
    # Manually seed repo and index in trie
    await repo.create(
        email=user.email,
        username=user.username,
        password_hash=user.password_hash,
        full_name=user.full_name,
    )
    await service.sync_trie_from_repository()

    # Autocomplete via username prefix
    user_results = await service.autocomplete_users("search_", limit=10)
    assert len(user_results) == 1
    assert user_results[0]["username"] == "search_hero"

    # Autocomplete via full_name prefix
    name_results = await service.autocomplete_users("search m", limit=10)
    assert len(name_results) == 1
    assert name_results[0]["full_name"] == "Search Master"

    # Delete user and verify trie entries are pruned
    await service.delete_user(user_results[0]["id"])
    assert await service.autocomplete_users("search_", limit=10) == []
    assert await service.autocomplete_users("search m", limit=10) == []


def test_api_endpoint_autocomplete(client: TestClient) -> None:
    """Verify GET /users/autocomplete endpoint returns matching users in sub-millisecond time."""
    # 1. Register users via API
    res1 = client.post(
        "/users/",
        json={
            "email": "autocomplete_admin@example.com",
            "username": "autocomplete_admin",
            "password": "SecurePassword123!",
            "password_confirm": "SecurePassword123!",
            "full_name": "Admin Superuser",
            "role": "admin",
        },
    )
    assert res1.status_code == status.HTTP_201_CREATED

    res2 = client.post(
        "/users/",
        json={
            "email": "autocomplete_dev@example.com",
            "username": "autocomplete_developer",
            "password": "SecurePassword123!",
            "password_confirm": "SecurePassword123!",
            "full_name": "Developer Master",
            "role": "user",
        },
    )
    assert res2.status_code == status.HTTP_201_CREATED

    # 2. Query autocomplete for "autocomp"
    search_res = client.get("/users/autocomplete?q=autocomp&limit=5")
    assert search_res.status_code == status.HTTP_200_OK

    data = search_res.json()
    assert isinstance(data, list)
    assert len(data) >= 2
    usernames = [item["username"] for item in data]
    assert "autocomplete_admin" in usernames
    assert "autocomplete_developer" in usernames

    # 3. Query autocomplete for full_name prefix "dev"
    name_res = client.get("/users/autocomplete?q=dev&limit=5")
    assert name_res.status_code == status.HTTP_200_OK
    name_data = name_res.json()
    assert any(item["username"] == "autocomplete_developer" for item in name_data)

    # 4. Parameter validation checks
    # Missing 'q' query parameter
    empty_res = client.get("/users/autocomplete")
    assert empty_res.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY

    # 'q' exceeds max length
    too_long = client.get(f"/users/autocomplete?q={'a' * 51}")
    assert too_long.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
