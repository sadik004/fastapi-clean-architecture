"""Unit tests for InMemoryUserRepository and index synchronization mechanics."""

import pytest

from app.repositories.user_repository import InMemoryUserRepository, UserEntity


@pytest.mark.asyncio
async def test_repository_create_and_get() -> None:
    """Verify entity creation and O(1) retrieval by ID, email, and username."""
    repo = InMemoryUserRepository()
    user = await repo.create(
        email="test@example.com",
        username="test_user",
        password_hash="hash_12345",
        age=25,
        role="user",
    )
    assert isinstance(user, UserEntity)
    assert user.id == 1
    assert user.email == "test@example.com"
    assert user.username == "test_user"

    # O(1) lookups
    assert await repo.get_by_id(1) == user
    assert await repo.get_by_email("test@example.com") == user
    assert await repo.get_by_username("test_user") == user

    # Non-existent lookups
    assert await repo.get_by_id(999) is None
    assert await repo.get_by_email("unknown@example.com") is None
    assert await repo.get_by_username("unknown_user") is None


@pytest.mark.asyncio
async def test_repository_update_synchronizes_indexes() -> None:
    """Verify that updating email and username frees old index entries and maps new ones."""
    repo = InMemoryUserRepository()
    user = await repo.create(
        email="old_email@example.com",
        username="old_handle",
        password_hash="hash_12345",
        age=20,
    )

    updated = await repo.update(
        user_id=user.id,
        email="new_email@example.com",
        username="new_handle",
        age=21,
        role="admin",
    )
    assert updated is not None
    assert updated.email == "new_email@example.com"
    assert updated.username == "new_handle"
    assert updated.age == 21
    assert updated.role == "admin"

    # Old keys must be purged from secondary inverted indexes
    assert await repo.get_by_email("old_email@example.com") is None
    assert await repo.get_by_username("old_handle") is None

    # New keys must be mapped in secondary inverted indexes
    assert await repo.get_by_email("new_email@example.com") == updated
    assert await repo.get_by_username("new_handle") == updated


@pytest.mark.asyncio
async def test_repository_update_non_existent() -> None:
    """Verify updating a non-existent user returns None."""
    repo = InMemoryUserRepository()
    assert await repo.update(user_id=999, age=30) is None


@pytest.mark.asyncio
async def test_repository_delete_purges_secondary_indexes() -> None:
    """Verify deleting a user purges primary store and both secondary indexes."""
    repo = InMemoryUserRepository()
    user = await repo.create(
        email="purge@example.com",
        username="purge_me",
        password_hash="hash_secret",
    )

    # Deletion succeeds
    assert await repo.delete(user.id) is True

    # Primary store and secondary indexes must be purged
    assert await repo.get_by_id(user.id) is None
    assert await repo.get_by_email("purge@example.com") is None
    assert await repo.get_by_username("purge_me") is None

    # Critical invariant: subsequent creation with freed email & username succeeds
    recreated_user = await repo.create(
        email="purge@example.com",
        username="purge_me",
        password_hash="new_hash",
    )
    assert recreated_user.id == 2
    assert await repo.get_by_email("purge@example.com") == recreated_user
    assert await repo.get_by_username("purge_me") == recreated_user


@pytest.mark.asyncio
async def test_repository_delete_non_existent() -> None:
    """Verify deleting non-existent ID returns False without error."""
    repo = InMemoryUserRepository()
    assert await repo.delete(9999) is False


@pytest.mark.asyncio
async def test_repository_pagination_slicing() -> None:
    """Verify list_all respects limit and offset."""
    repo = InMemoryUserRepository()
    for i in range(5):
        await repo.create(
            email=f"user{i}@example.com",
            username=f"user_{i}",
            password_hash="pass",
        )

    # Page 1 (limit=2, offset=0)
    page1 = await repo.list_all(limit=2, offset=0)
    assert len(page1) == 2
    assert [u.username for u in page1] == ["user_0", "user_1"]

    # Page 2 (limit=2, offset=2)
    page2 = await repo.list_all(limit=2, offset=2)
    assert len(page2) == 2
    assert [u.username for u in page2] == ["user_2", "user_3"]

    # Page 3 (limit=2, offset=4)
    page3 = await repo.list_all(limit=2, offset=4)
    assert len(page3) == 1
    assert [u.username for u in page3] == ["user_4"]

    # Offset out of bounds
    page_empty = await repo.list_all(limit=2, offset=10)
    assert len(page_empty) == 0


@pytest.mark.asyncio
async def test_repository_clear() -> None:
    """Verify clear() resets all state."""
    repo = InMemoryUserRepository()
    await repo.create(email="clear@example.com", username="clear_user", password_hash="h")
    repo.clear()

    assert await repo.list_all() == []
    assert await repo.get_by_email("clear@example.com") is None
    assert await repo.get_by_username("clear_user") is None
