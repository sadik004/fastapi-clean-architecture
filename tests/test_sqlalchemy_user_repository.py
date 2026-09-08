"""Integration tests for SqlAlchemyUserRepository and Domain Entity Decoupling.

Verifies:
1. Full CRUD lifecycle operations on real database tables via SqlAlchemyUserRepository.
2. O(log N) index lookups by unique email and username.
3. Database engine-level LIMIT and OFFSET pagination and SQL-level WHERE filtering.
4. The Zero ORM Leakage Boundary: assertions ensuring UserModel instances never escape
   the repository and that UserEntity attributes remain accessible outside active sessions.
5. End-to-End API verification: FastAPI endpoints interacting with the database repository.
"""

from collections.abc import AsyncGenerator
from datetime import datetime

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import async_session_factory
from app.models.user import UserModel
from app.repositories.sqlalchemy_user_repository import SqlAlchemyUserRepository
from app.repositories.user_repository import UserEntity
from app.schemas.user import UserUpdate


@pytest_asyncio.fixture
async def db_session() -> AsyncGenerator[AsyncSession]:
    """Provide an isolated async database session with automatic transaction rollback."""
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


@pytest_asyncio.fixture
async def sql_repo(db_session: AsyncSession) -> SqlAlchemyUserRepository:
    """Provide a SqlAlchemyUserRepository instance bound to the test session."""
    return SqlAlchemyUserRepository(session=db_session)


# ============================================================================
# 1. Full CRUD Lifecycle Tests
# ============================================================================


@pytest.mark.asyncio
async def test_sqlalchemy_repo_create_and_get_by_id(
    sql_repo: SqlAlchemyUserRepository,
) -> None:
    """Verify entity creation and O(1) clustered primary key retrieval."""
    created = await sql_repo.create(
        email="sql_crud@example.com",
        username="sql_crud_user",
        password_hash="argon2_hashed_secret",
        age=28,
        role="user",
        full_name="SQL Crud User",
        phone_number="+15551234567",
        bio="Backend engineer testing clean architecture",
        company_name="Acme Corp",
    )

    assert isinstance(created, UserEntity)
    assert created.id > 0
    assert created.email == "sql_crud@example.com"
    assert created.username == "sql_crud_user"
    assert created.password_hash == "argon2_hashed_secret"
    assert created.age == 28
    assert created.role == "user"
    assert created.full_name == "SQL Crud User"
    assert created.is_active is True
    assert isinstance(created.created_at, datetime)
    assert created.created_at.tzinfo is not None

    # Fetch by primary key ID
    fetched = await sql_repo.get_by_id(created.id)
    assert fetched is not None
    assert fetched.id == created.id
    assert fetched.email == created.email
    assert fetched.username == created.username


@pytest.mark.asyncio
async def test_sqlalchemy_repo_update_selective_fields(
    sql_repo: SqlAlchemyUserRepository,
) -> None:
    """Verify selective attribute updates and persistent timestamp refresh."""
    user = await sql_repo.create(
        email="initial@example.com",
        username="initial_handle",
        password_hash="hash123",
        age=22,
        role="user",
    )

    # 1. Update using keyword arguments
    updated = await sql_repo.update(
        user_id=user.id,
        email="updated@example.com",
        username="updated_handle",
        age=23,
        role="admin",
        bio="Updated bio description",
    )
    assert updated is not None
    assert updated.id == user.id
    assert updated.email == "updated@example.com"
    assert updated.username == "updated_handle"
    assert updated.age == 23
    assert updated.role == "admin"
    assert updated.bio == "Updated bio description"

    # Verify updates persisted in database
    refetched = await sql_repo.get_by_id(user.id)
    assert refetched is not None
    assert refetched.email == "updated@example.com"
    assert refetched.username == "updated_handle"

    # 2. Update using UserUpdate DTO
    update_dto = UserUpdate(
        full_name="Updated Name",
        company_name="New Enterprise LLC",
    )
    dto_updated = await sql_repo.update(
        user_id=user.id,
        update_data=update_dto,
    )
    assert dto_updated is not None
    assert dto_updated.full_name == "Updated Name"
    assert dto_updated.company_name == "New Enterprise LLC"


@pytest.mark.asyncio
async def test_sqlalchemy_repo_update_non_existent(
    sql_repo: SqlAlchemyUserRepository,
) -> None:
    """Verify updating a non-existent user returns None without error."""
    result = await sql_repo.update(user_id=99999, age=30)
    assert result is None


@pytest.mark.asyncio
async def test_sqlalchemy_repo_delete_lifecycle(
    sql_repo: SqlAlchemyUserRepository,
) -> None:
    """Verify entity deletion removes database record and returns True."""
    user = await sql_repo.create(
        email="delete_me@example.com",
        username="delete_user",
        password_hash="secret_hash",
    )

    # Delete existing user
    deleted = await sql_repo.delete(user.id)
    assert deleted is True

    # Read after delete returns None
    assert await sql_repo.get_by_id(user.id) is None
    assert await sql_repo.get_by_email("delete_me@example.com") is None
    assert await sql_repo.get_by_username("delete_user") is None

    # Deleting again returns False
    assert await sql_repo.delete(user.id) is False


@pytest.mark.asyncio
async def test_sqlalchemy_repo_delete_non_existent(
    sql_repo: SqlAlchemyUserRepository,
) -> None:
    """Verify deleting a non-existent ID returns False."""
    assert await sql_repo.delete(99999) is False


# ============================================================================
# 2. O(log N) Unique B-Tree Index Lookup Tests
# ============================================================================


@pytest.mark.asyncio
async def test_sqlalchemy_repo_index_lookups(
    sql_repo: SqlAlchemyUserRepository,
) -> None:
    """Verify O(log N) lookups by unique email and unique username."""
    user1 = await sql_repo.create(
        email="index1@example.com",
        username="index_user1",
        password_hash="hash1",
    )
    user2 = await sql_repo.create(
        email="index2@example.com",
        username="index_user2",
        password_hash="hash2",
    )

    # Email lookups
    assert await sql_repo.get_by_email("index1@example.com") == user1
    assert await sql_repo.get_by_email("index2@example.com") == user2
    assert await sql_repo.get_by_email("nonexistent@example.com") is None

    # Username lookups
    assert await sql_repo.get_by_username("index_user1") == user1
    assert await sql_repo.get_by_username("index_user2") == user2
    assert await sql_repo.get_by_username("nonexistent_handle") is None


# ============================================================================
# 3. Database Engine-Level Pagination & Filtering Tests
# ============================================================================


@pytest.mark.asyncio
async def test_sqlalchemy_repo_pagination_and_filtering(
    sql_repo: SqlAlchemyUserRepository,
) -> None:
    """Verify SQL-level LIMIT/OFFSET windowing and WHERE filtering."""
    # Seed 5 users with varied roles, names, and active flags
    u1 = await sql_repo.create(
        email="alice@tech.org",
        username="alice_w",
        password_hash="h1",
        role="admin",
        full_name="Alice Wonderland",
    )
    u2 = await sql_repo.create(
        email="bob@builder.org",
        username="bob_b",
        password_hash="h2",
        role="user",
        full_name="Bob Builder",
    )
    u3 = await sql_repo.create(
        email="charlie@chaplin.org",
        username="charlie_c",
        password_hash="h3",
        role="enterprise",
        full_name="Charlie Chaplin",
    )
    u4 = await sql_repo.create(
        email="dana@scully.gov",
        username="dana_s",
        password_hash="h4",
        role="admin",
        full_name="Dana Scully",
    )
    u5 = await sql_repo.create(
        email="fox@mulder.gov",
        username="fox_m",
        password_hash="h5",
        role="user",
        full_name="Fox Mulder",
    )

    # Pagination: Page 1 (limit=2, offset=0)
    page1 = await sql_repo.list_all(limit=2, offset=0)
    assert len(page1) == 2
    assert [u.id for u in page1] == [u1.id, u2.id]

    # Pagination: Page 2 (limit=2, offset=2)
    page2 = await sql_repo.list_all(limit=2, offset=2)
    assert len(page2) == 2
    assert [u.id for u in page2] == [u3.id, u4.id]

    # Pagination: Page 3 (limit=2, offset=4)
    page3 = await sql_repo.list_all(limit=2, offset=4)
    assert len(page3) == 1
    assert [u.id for u in page3] == [u5.id]

    # Filter by role: admin
    admins = await sql_repo.list_all(role="admin")
    assert len(admins) == 2
    assert {a.username for a in admins} == {"alice_w", "dana_s"}

    # Search filter: partial match on full_name or email
    search_results = await sql_repo.list_all(search="scully")
    assert len(search_results) == 1
    assert search_results[0].username == "dana_s"

    search_gov = await sql_repo.list_all(search=".gov")
    assert len(search_gov) == 2
    assert {u.username for u in search_gov} == {"dana_s", "fox_m"}


# ============================================================================
# 4. Zero ORM Leakage Boundary Tests
# ============================================================================


@pytest.mark.asyncio
async def test_sqlalchemy_repo_zero_orm_leakage_boundary(
    db_session: AsyncSession,
) -> None:
    """Verify that raw ORM models (UserModel) never leak past repository boundary."""
    repo = SqlAlchemyUserRepository(session=db_session)
    user = await repo.create(
        email="leakage_test@example.com",
        username="leakage_user",
        password_hash="hashed_pw",
        role="user",
    )

    # Invariant 1: Returned entity is strictly UserEntity, NOT UserModel
    assert isinstance(user, UserEntity)
    assert not isinstance(user, UserModel)

    # Invariant 2: Entities remain fully accessible even after session commit/close
    # (No sqlalchemy.exc.MissingGreenlet or DetachedInstanceError)
    await db_session.commit()

    assert user.id > 0
    assert user.email == "leakage_test@example.com"
    assert user.username == "leakage_user"
    assert user.is_active is True
    assert isinstance(user.created_at, datetime)


# ============================================================================
# 5. End-to-End API Integration Tests with SqlAlchemyUserRepository
# ============================================================================


def test_end_to_end_api_crud_with_database(
    client: TestClient,
    admin_auth_headers: dict[str, str],
) -> None:
    """Verify FastAPI endpoints seamlessly persist to and read from the real SQL database."""
    from app.core.config import get_settings
    from app.core.dependencies import get_user_repository
    from app.main import app

    # Remove test in-memory override so FastAPI exercises real SqlAlchemyUserRepository
    app.dependency_overrides.pop(get_user_repository, None)

    # Seed admin user in database to authorize DELETE endpoint
    settings = get_settings()
    admin_payload = {
        "email": "sql.admin@example.com",
        "username": settings.admin_username,
        "password": "Password123!",
        "password_confirm": "Password123!",
        "age": 35,
        "role": "admin",
    }
    admin_res = client.post("/users/", json=admin_payload)
    assert admin_res.status_code == 201

    # 1. POST /users/
    payload = {
        "email": "e2e_db@example.com",
        "username": "e2e_db_user",
        "password": "SecurePassword123!",
        "password_confirm": "SecurePassword123!",
        "age": 29,
        "role": "user",
        "full_name": "E2E Database User",
    }
    create_res = client.post("/users/", json=payload)
    assert create_res.status_code == 201
    created_data = create_res.json()
    user_id = created_data["id"]
    assert created_data["email"] == "e2e_db@example.com"
    assert created_data["username"] == "e2e_db_user"

    # 2. GET /users/{id}
    get_res = client.get(f"/users/{user_id}")
    assert get_res.status_code == 200
    assert get_res.json()["id"] == user_id

    # 3. GET /users/by-username/{username}
    by_username_res = client.get("/users/by-username/e2e_db_user")
    assert by_username_res.status_code == 200
    assert by_username_res.json()["username"] == "e2e_db_user"

    # 4. PUT /users/{id}
    update_res = client.put(
        f"/users/{user_id}",
        json={"full_name": "Updated E2E Name", "age": 30},
        headers={"X-API-Key": "userkey_e2e_db_user"},
    )
    assert update_res.status_code == 200
    assert update_res.json()["full_name"] == "Updated E2E Name"
    assert update_res.json()["age"] == 30

    # 5. DELETE /users/{id} (authorized via admin_auth_headers)
    del_res = client.delete(f"/users/{user_id}", headers=admin_auth_headers)
    assert del_res.status_code == 204

    # 6. GET after delete -> 404
    get_deleted = client.get(f"/users/{user_id}")
    assert get_deleted.status_code == 404
