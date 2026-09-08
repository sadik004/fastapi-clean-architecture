"""Tests for Day 19: Preventing the N+1 Query Problem with selectinload, joinedload & Defensive lazy="raise".

Verifies:
1. Exact SQL query count assertion: 10 users with 50 posts loaded via selectinload emits
   EXACTLY 2 SELECT queries (defeating the 1 + 10 = 11 N+1 query leak).
2. Many-to-One scalar eager loading: joinedload emits EXACTLY 1 SELECT query with LEFT OUTER JOIN.
3. Defensive barrier: accessing un-eagerly loaded relationships triggers InvalidRequestError
   due to lazy="raise", preventing silent performance degradation.
4. Schema migration verification and zero schema drift for the posts table.
"""

from collections.abc import AsyncGenerator, Generator
from contextlib import contextmanager
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import event, inspect, select
from sqlalchemy.engine import Connection, ExecutionContext
from sqlalchemy.exc import InvalidRequestError
from sqlalchemy.ext.asyncio import AsyncSession

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from app.core.database import (
    Base,
    apply_migrations,
    async_session_factory,
    engine,
    rollback_migration,
)
from app.models.post import PostModel
from app.models.user import UserModel
from app.repositories.post_repository import PostEntity, SqlAlchemyPostRepository
from app.repositories.sqlalchemy_user_repository import SqlAlchemyUserRepository
from app.repositories.user_repository import UserWithPostsEntity

# ============================================================================
# Query Counting Context Manager
# ============================================================================


@contextmanager
def capture_queries() -> Generator[list[str]]:
    """Capture raw SQL statements executed by the underlying database engine."""
    queries: list[str] = []

    def before_cursor_execute(
        conn: Connection,
        cursor: Any,
        statement: str,
        parameters: Any,
        context: ExecutionContext,
        executemany: bool,
    ) -> None:
        queries.append(statement.strip())

    event.listen(engine.sync_engine, "before_cursor_execute", before_cursor_execute)
    try:
        yield queries
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", before_cursor_execute)


# ============================================================================
# Test Fixtures
# ============================================================================


@pytest_asyncio.fixture
async def db_session() -> AsyncGenerator[AsyncSession]:
    """Provide an isolated async database session with automatic transaction commit/rollback."""
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


@pytest_asyncio.fixture
async def user_repo(db_session: AsyncSession) -> SqlAlchemyUserRepository:
    """Provide a SqlAlchemyUserRepository instance."""
    return SqlAlchemyUserRepository(session=db_session)


@pytest_asyncio.fixture
async def post_repo(db_session: AsyncSession) -> SqlAlchemyPostRepository:
    """Provide a SqlAlchemyPostRepository instance."""
    return SqlAlchemyPostRepository(session=db_session)


# ============================================================================
# 1. 1-to-Many Collection Eager Loading (selectinload) Query Budget Test
# ============================================================================


@pytest.mark.asyncio
async def test_selectinload_defeats_n_plus_one_with_exact_two_queries(
    db_session: AsyncSession,
    user_repo: SqlAlchemyUserRepository,
    post_repo: SqlAlchemyPostRepository,
) -> None:
    """Mathematical proof: loading 10 users with 50 posts executes EXACTLY 2 SELECT queries.

    Without selectinload:
      - 1 initial query for 10 users
      - 10 secondary queries (one per user to fetch posts)
      - Total: 1 + 10 = 11 queries (N+1 query problem)

    With selectinload:
      - Query 1: SELECT users ... LIMIT 10
      - Query 2: SELECT posts ... WHERE user_id IN (:user_id_1, ..., :user_id_10)
      - Total: Strictly 2 queries regardless of N!
    """
    # Seed 10 users, each with 5 posts (50 posts total)
    user_ids: list[int] = []
    for i in range(10):
        u = await user_repo.create(
            email=f"nplusone_{i}@example.com",
            username=f"nplusone_user_{i}",
            password_hash=f"hash_{i}",
            role="user",
        )
        user_ids.append(u.id)
        for j in range(5):
            await post_repo.create(
                title=f"Post {j} by User {i}",
                content=f"Detailed body content for post {j} authored by user {i}",
                user_id=u.id,
            )

    # Commit seeding transaction to ensure fresh database read
    await db_session.commit()

    # Open a fresh session to evaluate eager loading query emission
    async with async_session_factory() as test_session:
        fresh_repo = SqlAlchemyUserRepository(session=test_session)

        with capture_queries() as captured:
            results: list[UserWithPostsEntity] = await fresh_repo.list_users_with_posts(
                limit=10,
                offset=0,
            )

        # Filter captured queries to SELECT statements only
        select_queries = [q for q in captured if q.upper().startswith("SELECT")]

        # 1. Verify entity cardinality and child relationship correctness
        assert len(results) == 10, f"Expected 10 users, got {len(results)}"
        for user_entity in results:
            assert isinstance(user_entity, UserWithPostsEntity)
            assert len(user_entity.posts) == 5, (
                f"User {user_entity.username} expected 5 posts, got {len(user_entity.posts)}"
            )
            for p in user_entity.posts:
                assert isinstance(p, PostEntity)
                assert p.user_id == user_entity.id

        # 2. Mathematical Proof: Exactly 2 SELECT queries emitted
        assert len(select_queries) == 2, (
            f"Expected exactly 2 SELECT queries (selectinload), but {len(select_queries)} were emitted:\n"
            + "\n---\n".join(select_queries)
        )

        # Verify query structures
        assert "FROM users" in select_queries[0]
        assert "FROM posts" in select_queries[1]
        assert "WHERE posts.user_id IN" in select_queries[1]


# ============================================================================
# 2. Many-to-One Scalar Eager Loading (joinedload) Query Budget Test
# ============================================================================


@pytest.mark.asyncio
async def test_joinedload_scalar_executes_single_query(
    db_session: AsyncSession,
    user_repo: SqlAlchemyUserRepository,
    post_repo: SqlAlchemyPostRepository,
) -> None:
    """Verify that joinedload on Many-to-One author executes EXACTLY 1 query with LEFT OUTER JOIN."""
    # Seed user and post
    author = await user_repo.create(
        email="author_joined@example.com",
        username="author_joined",
        password_hash="hash_author",
        full_name="Author Joined",
    )
    post = await post_repo.create(
        title="Scalar Joinedload Title",
        content="Scalar joinedload content body",
        user_id=author.id,
    )
    await db_session.commit()

    # Query with joinedload in a fresh session
    async with async_session_factory() as test_session:
        fresh_post_repo = SqlAlchemyPostRepository(session=test_session)

        with capture_queries() as captured:
            result = await fresh_post_repo.get_post_with_author(post.id)

        select_queries = [q for q in captured if q.upper().startswith("SELECT")]

        # 1. Assert result integrity and Zero ORM Leakage
        assert result is not None
        assert isinstance(result, PostEntity)
        assert result.author is not None
        assert result.author.id == author.id
        assert result.author.email == "author_joined@example.com"
        assert result.author.full_name == "Author Joined"

        # 2. Assert exactly 1 query with LEFT OUTER JOIN
        assert len(select_queries) == 1, (
            f"Expected exactly 1 SELECT query (joinedload), but {len(select_queries)} were emitted:\n"
            + "\n---\n".join(select_queries)
        )
        assert "JOIN users" in select_queries[0] or "OUTER JOIN users" in select_queries[0]


# ============================================================================
# 3. Defensive Barrier: lazy="raise" Runtime Enforcement
# ============================================================================


@pytest.mark.asyncio
async def test_lazy_raise_prevents_implicit_lazy_loading(
    db_session: AsyncSession,
    user_repo: SqlAlchemyUserRepository,
    post_repo: SqlAlchemyPostRepository,
) -> None:
    """Verify that lazy='raise' raises InvalidRequestError if relationships are accessed without eager loading."""
    user = await user_repo.create(
        email="lazy_raise@example.com",
        username="lazy_raise_user",
        password_hash="hash_lazy",
    )
    post = await post_repo.create(
        title="Lazy Post",
        content="Post content for lazy test",
        user_id=user.id,
    )
    await db_session.commit()

    # 1. Test un-eagerly loaded UserModel.posts
    async with async_session_factory() as test_session:
        user_stmt = select(UserModel).where(UserModel.id == user.id)
        res_user = await test_session.execute(user_stmt)
        user_model = res_user.scalar_one()

        with pytest.raises(InvalidRequestError, match=r"UserModel\.posts.*lazy='raise'"):
            _ = user_model.posts

    # 2. Test un-eagerly loaded PostModel.author
    async with async_session_factory() as test_session:
        post_stmt = select(PostModel).where(PostModel.id == post.id)
        res_post = await test_session.execute(post_stmt)
        post_model = res_post.scalar_one()

        with pytest.raises(InvalidRequestError, match=r"PostModel\.author.*lazy='raise'"):
            _ = post_model.author


# ============================================================================
# 4. Schema Migration & Drift Verification for Posts Table
# ============================================================================


@pytest.mark.asyncio
async def test_posts_table_schema_migration_lifecycle() -> None:
    """Verify bi-directional migration reversibility and zero schema drift for posts table."""
    # Ensure current head is applied
    apply_migrations(revision="head")

    async with engine.connect() as conn:
        # 1. Verify posts table and indexes exist
        def inspect_schema(sync_conn: Connection) -> dict[str, Any]:
            inspector = inspect(sync_conn)
            return {
                "tables": inspector.get_table_names(),
                "columns": [c["name"] for c in inspector.get_columns("posts")],
                "indexes": [idx["name"] for idx in inspector.get_indexes("posts")],
            }

        schema_info = await conn.run_sync(inspect_schema)
        assert "posts" in schema_info["tables"]
        assert "id" in schema_info["columns"]
        assert "title" in schema_info["columns"]
        assert "content" in schema_info["columns"]
        assert "user_id" in schema_info["columns"]
        assert "created_at" in schema_info["columns"]
        assert any("ix_posts_user_id" in str(idx) for idx in schema_info["indexes"])

        # 2. Verify zero schema drift between target_metadata and database
        def check_drift(sync_conn: Connection) -> list[Any]:
            migration_context = MigrationContext.configure(sync_conn)
            return list(compare_metadata(migration_context, Base.metadata))

        drift = await conn.run_sync(check_drift)
        assert drift == [], f"Detected unexpected schema drift: {drift}"

    # 3. Test reversibility: downgrade by 1 revision (drops posts table)
    rollback_migration(revision="-1")
    async with engine.connect() as conn:
        tables_after_rollback = await conn.run_sync(lambda sync_conn: inspect(sync_conn).get_table_names())
        assert "posts" not in tables_after_rollback
        assert "users" in tables_after_rollback

    # 4. Re-apply to head
    apply_migrations(revision="head")
    async with engine.connect() as conn:
        tables_restored = await conn.run_sync(lambda sync_conn: inspect(sync_conn).get_table_names())
        assert "posts" in tables_restored
