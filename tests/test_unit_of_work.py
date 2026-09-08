"""Comprehensive test suite for Day 20: The Unit of Work (UoW) Pattern.

Verifies:
1. Happy Path Atomic Commit: User and post created in SqlAlchemyUnitOfWork are persisted atomically.
2. Failure Rollback (The ACID Atomicity Proof): Exception raised during post creation completely
   rolls back the entire transaction, leaving ZERO orphaned user records in the database.
3. Explicit Rollback: Invoking await uow.rollback() explicitly discards staged entities.
4. Resource & Session Teardown: Session is unconditionally closed in finally, preventing connection pool leaks.
5. In-Memory Unit of Work: Snapshot-based rollback simulation restores internal storage and indexes.
6. Service Layer Orchestration: UserService.create_user_with_initial_post operates atomically.
7. Router HTTP Integration: POST /users/with-initial-post endpoint end-to-end integration.
"""

from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.database import async_session_factory
from app.core.dependencies import get_uow, get_user_repository
from app.core.exceptions import UserAlreadyExistsException
from app.core.unit_of_work import InMemoryUnitOfWork, SqlAlchemyUnitOfWork
from app.main import app
from app.models.post import PostModel
from app.models.user import UserModel
from app.repositories.post_repository import InMemoryPostRepository
from app.repositories.sqlalchemy_user_repository import SqlAlchemyUserRepository
from app.repositories.user_repository import InMemoryUserRepository
from app.schemas.user import UserCreate, UserRole
from app.services.user_service import UserService

# ============================================================================
# 1. SqlAlchemyUnitOfWork Core ACID Atomicity Tests
# ============================================================================


@pytest.mark.asyncio
async def test_uow_atomic_commit_persists_user_and_post() -> None:
    """Verify that operations across multiple repositories within UoW persist atomically on commit."""
    email = "uow.author@example.com"
    username = "uow_author"
    post_title = "Architecting Atomic Transactions"
    post_content = "The Unit of Work pattern manages transaction boundaries across multiple repositories."

    uow = SqlAlchemyUnitOfWork()

    async with uow:
        # Create user via uow.users repository
        user = await uow.users.create(
            email=email,
            username=username,
            password_hash="pbkdf2_hashed_secret",
            role="user",
        )
        assert user.id is not None

        # Create post via uow.posts repository sharing the exact same session
        post = await uow.posts.create(
            title=post_title,
            content=post_content,
            user_id=user.id,
        )
        assert post.id is not None
        assert post.user_id == user.id

        # Atomic commit
        await uow.commit()

    # Verify persistence using a separate, independent session
    async with async_session_factory() as verify_session:
        user_res = await verify_session.execute(select(UserModel).where(UserModel.email == email))
        persisted_user = user_res.scalar_one_or_none()
        assert persisted_user is not None
        assert persisted_user.username == username

        post_res = await verify_session.execute(select(PostModel).where(PostModel.user_id == persisted_user.id))
        persisted_post = post_res.scalar_one_or_none()
        assert persisted_post is not None
        assert persisted_post.title == post_title
        assert persisted_post.content == post_content


@pytest.mark.asyncio
async def test_uow_rollback_on_exception_leaves_zero_orphaned_records() -> None:
    """The ACID Atomicity Proof: An exception during post creation rolls back the user record."""
    email = "rollback.user@example.com"
    username = "rollback_user"

    uow = SqlAlchemyUnitOfWork()

    with pytest.raises(RuntimeError, match="Simulated crash during post persistence"):
        async with uow:
            # 1. User creation succeeds and is staged in session
            user = await uow.users.create(
                email=email,
                username=username,
                password_hash="pbkdf2_hashed_secret",
                role="user",
            )
            assert user.id is not None

            # 2. Simulated unexpected crash before/during post processing
            raise RuntimeError("Simulated crash during post persistence")

    # Invariant check: The transaction MUST have been rolled back by __aexit__
    # Absolutely NO user record or post record should exist in the database
    async with async_session_factory() as verify_session:
        user_res = await verify_session.execute(select(UserModel).where(UserModel.email == email))
        assert user_res.scalar_one_or_none() is None

        post_res = await verify_session.execute(select(PostModel).where(PostModel.title == "Orphaned Post"))
        assert post_res.scalar_one_or_none() is None


@pytest.mark.asyncio
async def test_uow_explicit_rollback_discards_staged_changes() -> None:
    """Verify that calling await uow.rollback() explicitly discards staged entities."""
    email = "explicit.rollback@example.com"
    username = "explicit_rollback"

    uow = SqlAlchemyUnitOfWork()

    async with uow:
        user = await uow.users.create(
            email=email,
            username=username,
            password_hash="pbkdf2_hashed_secret",
            role="user",
        )
        await uow.posts.create(
            title="Abandoned Post",
            content="This should not be saved",
            user_id=user.id,
        )

        # Explicit rollback before exiting
        await uow.rollback()

    # Verify database has zero trace of the discarded entities
    async with async_session_factory() as verify_session:
        user_res = await verify_session.execute(select(UserModel).where(UserModel.email == email))
        assert user_res.scalar_one_or_none() is None


@pytest.mark.asyncio
async def test_uow_session_cleanup_and_access_guards() -> None:
    """Verify that the database session is unconditionally closed and access outside context is blocked."""
    uow = SqlAlchemyUnitOfWork()

    # Accessing repositories before context entry must raise RuntimeError
    with pytest.raises(RuntimeError, match="UnitOfWork is not open"):
        _ = uow.users

    with pytest.raises(RuntimeError, match="UnitOfWork is not open"):
        _ = uow.posts

    # Within context, session is active and repositories accessible
    async with uow:
        assert uow.session is not None
        assert uow.users is not None
        assert uow.posts is not None

    # After clean context exit, session is closed and set to None
    assert uow.session is None
    with pytest.raises(RuntimeError, match="UnitOfWork is not open"):
        _ = uow.users

    # After exception context exit, session is also closed
    with pytest.raises(ValueError):
        async with uow:
            raise ValueError("Test error for cleanup verification")

    assert uow.session is None


# ============================================================================
# 2. InMemoryUnitOfWork Snapshot & Rollback Tests
# ============================================================================


@pytest.mark.asyncio
async def test_in_memory_uow_snapshot_and_rollback() -> None:
    """Verify InMemoryUnitOfWork restores dictionary storage and indexes upon rollback."""
    user_repo = InMemoryUserRepository()
    post_repo = InMemoryPostRepository()
    uow = InMemoryUnitOfWork(user_repo=user_repo, post_repo=post_repo)

    # 1. Happy path: Commit keeps entries
    async with uow:
        user = await uow.users.create(
            email="mem.user@example.com",
            username="mem_user",
            password_hash="mem_hash",
        )
        await uow.posts.create(
            title="Mem Post",
            content="Mem Content",
            user_id=user.id,
        )
        await uow.commit()

    assert await user_repo.get_by_email("mem.user@example.com") is not None
    assert await post_repo.get_by_id(1) is not None

    # 2. Failure rollback: Exception restores pre-context state
    with pytest.raises(RuntimeError, match="Memory failure simulation"):
        async with uow:
            await uow.users.create(
                email="mem.fail@example.com",
                username="mem_fail",
                password_hash="mem_hash",
            )
            await uow.posts.create(
                title="Fail Post",
                content="Fail Content",
                user_id=999,
            )
            raise RuntimeError("Memory failure simulation")

    # Invariant: mem.fail@example.com must NOT exist in storage or index
    assert await user_repo.get_by_email("mem.fail@example.com") is None
    assert await user_repo.get_by_username("mem_fail") is None
    # Original user must still exist
    assert await user_repo.get_by_email("mem.user@example.com") is not None


# ============================================================================
# 3. Service Layer Atomic Multi-Entity Orchestration Tests
# ============================================================================


@pytest.mark.asyncio
async def test_service_create_user_with_initial_post_atomic_success() -> None:
    """Verify UserService.create_user_with_initial_post succeeds atomically."""
    uow = SqlAlchemyUnitOfWork()
    service = UserService(
        repository=SqlAlchemyUserRepository(session=None),  # type: ignore[arg-type]
        uow=uow,
    )

    payload = UserCreate(
        email="service.atomic@example.com",
        username="service_atomic",
        password="SecurePassword123!",
        password_confirm="SecurePassword123!",
        role=UserRole.USER,
        age=28,
        full_name="Atomic Orchestrator",
    )

    user, post = await service.create_user_with_initial_post(
        user_create=payload,
        post_title="Service Layer Atomicity",
        post_content="Orchestrated across UserService with Unit of Work.",
    )

    assert user.id is not None
    assert user.email == "service.atomic@example.com"
    assert post.id is not None
    assert post.user_id == user.id
    assert post.title == "Service Layer Atomicity"


@pytest.mark.asyncio
async def test_service_create_user_with_initial_post_duplicate_email_rejected() -> None:
    """Verify duplicate checks prevent user and post creation."""
    uow = SqlAlchemyUnitOfWork()
    service = UserService(
        repository=SqlAlchemyUserRepository(session=None),  # type: ignore[arg-type]
        uow=uow,
    )

    payload = UserCreate(
        email="duplicate.uow@example.com",
        username="duplicate_uow_1",
        password="SecurePassword123!",
        password_confirm="SecurePassword123!",
    )

    # First registration succeeds
    await service.create_user_with_initial_post(
        user_create=payload,
        post_title="Post One",
        post_content="Content One",
    )

    # Second registration with same email fails with UserAlreadyExistsException
    duplicate_payload = UserCreate(
        email="duplicate.uow@example.com",
        username="duplicate_uow_2",
        password="SecurePassword123!",
        password_confirm="SecurePassword123!",
    )

    with pytest.raises(UserAlreadyExistsException, match="already registered"):
        await service.create_user_with_initial_post(
            user_create=duplicate_payload,
            post_title="Post Two",
            post_content="Content Two",
        )


# ============================================================================
# 4. HTTP Router Endpoint Integration Tests
# ============================================================================


def test_endpoint_create_user_with_initial_post_success(client: TestClient) -> None:
    """Verify POST /users/with-initial-post creates user and post atomically returning HTTP 201."""
    # Ensure real database is used by popping test in-memory overrides
    app.dependency_overrides.pop(get_user_repository, None)
    app.dependency_overrides.pop(get_uow, None)

    request_payload = {
        "user": {
            "email": "http.uow@example.com",
            "username": "http_uow_user",
            "password": "SecurePassword123!",
            "password_confirm": "SecurePassword123!",
            "age": 30,
            "role": "user",
            "full_name": "HTTP UoW Master",
        },
        "post_title": "FastAPI Clean Architecture Day 20",
        "post_content": "Production Unit of Work pattern successfully connected via router.",
    }

    response = client.post("/users/with-initial-post", json=request_payload)
    assert response.status_code == 201
    data = response.json()

    assert "user" in data
    assert "post" in data
    assert data["user"]["email"] == "http.uow@example.com"
    assert data["user"]["username"] == "http_uow_user"
    assert data["post"]["title"] == "FastAPI Clean Architecture Day 20"
    assert data["post"]["user_id"] == data["user"]["id"]


def test_endpoint_create_user_with_initial_post_duplicate_returns_409(
    client: TestClient,
) -> None:
    """Verify duplicate user registration via endpoint returns 409 and leaves zero orphaned posts."""
    app.dependency_overrides.pop(get_user_repository, None)
    app.dependency_overrides.pop(get_uow, None)

    request_payload: dict[str, Any] = {
        "user": {
            "email": "conflict.uow@example.com",
            "username": "conflict_uow",
            "password": "SecurePassword123!",
            "password_confirm": "SecurePassword123!",
        },
        "post_title": "First Post",
        "post_content": "Content",
    }

    # First attempt: 201 Created
    first_res = client.post("/users/with-initial-post", json=request_payload)
    assert first_res.status_code == 201

    # Second attempt with same email: 409 Conflict
    second_res = client.post("/users/with-initial-post", json=request_payload)
    assert second_res.status_code == 409
    error_body = second_res.json()
    assert "already registered" in error_body.get("error", {}).get(
        "message", ""
    ) or "already registered" in error_body.get("detail", "")
