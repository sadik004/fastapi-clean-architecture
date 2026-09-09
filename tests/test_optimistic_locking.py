"""Test Suite for Day 39: Optimistic Concurrency Control (OCC) Architecture with Row Versioning.

Verifies:
1. Happy Path Version Increment: Updating an entity with expected_version=1 atomically
   increments version to 2 and returns HTTP 200 with updated fields.
2. Stale Version Rejection: Attempting an update with expected_version=1 on an entity that is
   already at version=2 raises OptimisticLockException / yields HTTP 409 Conflict with
   the CONCURRENCY_CONFLICT domain error envelope.
3. High-Concurrency Race Condition Invariant: Under 10 simultaneous concurrent requests
   targeting the same user at expected_version=1:
   - EXACTLY 1 request succeeds (HTTP 200, version=2)
   - EXACTLY 9 requests are rejected with HTTP 409 Conflict (CONCURRENCY_CONFLICT)
   - Mathematically proves zero Lost Updates!
4. Real Database Repository Atomicity: Direct SqlAlchemyUserRepository conditional UPDATE
   WHERE id=:id AND version=:expected_version strictly enforces rowcount == 0 detection.
"""

from __future__ import annotations

import asyncio

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.core.database import async_session_factory
from app.core.exceptions import OptimisticLockException
from app.main import app
from app.models.user import UserModel
from app.repositories.sqlalchemy_user_repository import SqlAlchemyUserRepository
from app.repositories.user_repository import InMemoryUserRepository
from app.schemas.user import UserCreate, UserUpdate
from app.services.user_service import UserService

# ============================================================================
# 1. Direct SQLAlchemy Repository Tests
# ============================================================================


@pytest.mark.asyncio
async def test_sqlalchemy_repo_optimistic_lock_happy_path() -> None:
    """Verify happy path: version 1 -> 2 increment on atomic conditional update."""
    async with async_session_factory() as session:
        repo = SqlAlchemyUserRepository(session=session)
        created = await repo.create(
            email="occ_happy@example.com",
            username="occ_happy_user",
            password_hash="hash_123",
            bio="Initial bio",
        )
        await session.commit()
        user_id = created.id
        assert created.version == 1

    # Execute conditional update with expected_version=1
    async with async_session_factory() as session:
        repo = SqlAlchemyUserRepository(session=session)
        updated = await repo.update_with_optimistic_lock(
            user_id=user_id,
            expected_version=1,
            update_data=UserUpdate(bio="Updated bio v2"),
        )
        await session.commit()

        assert updated.version == 2
        assert updated.bio == "Updated bio v2"

    # Verify persisted version in database
    async with async_session_factory() as session:
        stmt = select(UserModel).where(UserModel.id == user_id)
        result = await session.execute(stmt)
        model = result.scalar_one()
        assert model.version == 2
        assert model.bio == "Updated bio v2"


@pytest.mark.asyncio
async def test_sqlalchemy_repo_optimistic_lock_stale_version_rejected() -> None:
    """Verify stale version update raises OptimisticLockException immediately."""
    async with async_session_factory() as session:
        repo = SqlAlchemyUserRepository(session=session)
        created = await repo.create(
            email="occ_stale@example.com",
            username="occ_stale_user",
            password_hash="hash_123",
            bio="Initial bio",
        )
        await session.commit()
        user_id = created.id

    # Advance version from 1 to 2
    async with async_session_factory() as session:
        repo = SqlAlchemyUserRepository(session=session)
        await repo.update_with_optimistic_lock(
            user_id=user_id,
            expected_version=1,
            update_data=UserUpdate(bio="Version 2 update"),
        )
        await session.commit()

    # Attempt stale update with expected_version=1 on row that is now version=2
    async with async_session_factory() as session:
        repo = SqlAlchemyUserRepository(session=session)
        with pytest.raises(OptimisticLockException) as exc_info:
            await repo.update_with_optimistic_lock(
                user_id=user_id,
                expected_version=1,
                update_data=UserUpdate(bio="Stale overwrite attempt"),
            )
        assert exc_info.value.code == "CONCURRENCY_CONFLICT"
        assert "Stale version detected" in exc_info.value.message


@pytest.mark.asyncio
async def test_sqlalchemy_repo_optimistic_lock_missing_user_rejected() -> None:
    """Verify update on non-existent user raises OptimisticLockException due to rowcount == 0."""
    async with async_session_factory() as session:
        repo = SqlAlchemyUserRepository(session=session)
        with pytest.raises(OptimisticLockException):
            await repo.update_with_optimistic_lock(
                user_id=999_999,
                expected_version=1,
                update_data=UserUpdate(bio="Non-existent user"),
            )


# ============================================================================
# 2. In-Memory Repository & Service Layer OCC Tests
# ============================================================================


@pytest.mark.asyncio
async def test_in_memory_repo_optimistic_lock_lifecycle() -> None:
    """Verify in-memory repository protocol compliance and version increment."""
    repo = InMemoryUserRepository()
    service = UserService(repository=repo)

    user = await service.register_user(
        UserCreate(
            email="mem_occ@example.com",
            username="mem_occ_user",
            password="SecurePassword123!",
            password_confirm="SecurePassword123!",
            age=25,
        )
    )
    assert user.version == 1

    # Happy path update: expected_version=1 -> version 2
    updated = await service.update_user_optimistic(
        user_id=user.id,
        expected_version=1,
        payload=UserUpdate(bio="Memory bio v2"),
    )
    assert updated.version == 2
    assert updated.bio == "Memory bio v2"

    # Stale version update: expected_version=1 must be rejected
    with pytest.raises(OptimisticLockException) as exc_info:
        await service.update_user_optimistic(
            user_id=user.id,
            expected_version=1,
            payload=UserUpdate(bio="Stale attempt"),
        )
    assert exc_info.value.code == "CONCURRENCY_CONFLICT"


# ============================================================================
# 3. HTTP Transport & API Integration Tests
# ============================================================================


@pytest.mark.asyncio
async def test_api_optimistic_lock_endpoint_happy_and_stale() -> None:
    """Verify PUT /users/{user_id}/optimistic endpoint responses."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Create a user
        create_res = await client.post(
            "/users/",
            json={
                "email": "api_occ@example.com",
                "username": "api_occ_user",
                "password": "SecurePassword123!",
                "password_confirm": "SecurePassword123!",
                "age": 28,
            },
        )
        assert create_res.status_code == 201
        created_data = create_res.json()
        user_id = created_data["id"]
        assert created_data["version"] == 1

        # 2. Happy Path: Update with expected_version=1 -> HTTP 200, version=2
        update_res = await client.put(
            f"/users/{user_id}/optimistic?expected_version=1",
            json={"bio": "First OCC update via API"},
        )
        assert update_res.status_code == 200
        updated_data = update_res.json()
        assert updated_data["version"] == 2
        assert updated_data["bio"] == "First OCC update via API"

        # 3. Stale Rejection: Repeat update with expected_version=1 -> HTTP 409 Conflict
        stale_res = await client.put(
            f"/users/{user_id}/optimistic?expected_version=1",
            json={"bio": "Stale overwrite attempt"},
        )
        assert stale_res.status_code == 409
        error_body = stale_res.json()
        assert error_body["error"]["code"] == "CONCURRENCY_CONFLICT"
        assert "Stale version detected" in error_body["error"]["message"]

        # 4. Valid Sequential Update: expected_version=2 -> HTTP 200, version=3
        next_res = await client.put(
            f"/users/{user_id}/optimistic?expected_version=2",
            json={"bio": "Second OCC update via API"},
        )
        assert next_res.status_code == 200
        assert next_res.json()["version"] == 3


# ============================================================================
# 4. High-Concurrency Race Condition Test (Mathematical Invariant)
# ============================================================================


@pytest.mark.asyncio
async def test_high_concurrency_race_condition_eliminates_lost_updates() -> None:
    """Mathematical Invariant: 10 concurrent async updates targeting expected_version=1.

    Guarantees:
    - EXACTLY 1 request succeeds (HTTP 200) and advances row to version=2.
    - EXACTLY 9 requests fail with HTTP 409 Conflict (CONCURRENCY_CONFLICT).
    - Mathematically proves ZERO Lost Updates occur under concurrent write storms!
    """
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Create user
        create_res = await client.post(
            "/users/",
            json={
                "email": "race_occ@example.com",
                "username": "race_occ_user",
                "password": "SecurePassword123!",
                "password_confirm": "SecurePassword123!",
                "age": 30,
            },
        )
        assert create_res.status_code == 201
        user_id = create_res.json()["id"]

        concurrency_count = 10

        async def send_concurrent_update(index: int) -> int:
            res = await client.put(
                f"/users/{user_id}/optimistic?expected_version=1",
                json={"bio": f"Concurrent mutation candidate #{index}"},
            )
            return res.status_code

        # Fire all 10 requests concurrently via asyncio.gather
        tasks = [send_concurrent_update(i) for i in range(concurrency_count)]
        status_codes = await asyncio.gather(*tasks)

        # Assert Mathematical Invariants
        success_count = status_codes.count(200)
        conflict_count = status_codes.count(409)

        assert success_count == 1, (
            f"Expected EXACTLY 1 successful update (HTTP 200), got {success_count}. Statuses: {status_codes}"
        )
        assert conflict_count == 9, (
            f"Expected EXACTLY 9 conflict rejections (HTTP 409), got {conflict_count}. Statuses: {status_codes}"
        )

        # Verify final state
        get_res = await client.get(f"/users/{user_id}")
        assert get_res.status_code == 200
        final_data = get_res.json()
        assert final_data["version"] == 2
        assert "Concurrent mutation candidate" in final_data["bio"]
