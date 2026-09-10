"""Comprehensive test suite for Day 17: Automatic Async Database Schema Migration with Alembic.

Verifies:
1. Programmatic schema migration execution via apply_migrations().
2. Database schema verification asserting the 'users' table and indexes exist.
3. Bi-directional reversibility (downgrade to base/-1 drops table, upgrade to head restores it).
4. Schema drift detection via Alembic's compare_metadata against Base.metadata.
5. UserModel ORM entity persistence and automatic timestamp assignment.
"""

from typing import Any

import pytest
from sqlalchemy import inspect, select
from sqlalchemy.engine import Connection

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from app.core.database import (
    Base,
    apply_migrations,
    async_session_factory,
    engine,
    rollback_migration,
)
from app.models.user import UserModel

# ============================================================================
# 1. Programmatic Upgrade & Table Inspection Tests
# ============================================================================


@pytest.mark.asyncio
async def test_apply_migrations_creates_users_table_and_indexes() -> None:
    """Verify programmatic migration up to 'head' creates 'users' table and indexes."""
    # Run migrations up to head
    apply_migrations(revision="head")

    # Inspect schema via active engine connection
    async with engine.connect() as conn:

        def check_tables_and_indexes(sync_conn: Connection) -> dict[str, Any]:
            inspector = inspect(sync_conn)
            tables = inspector.get_table_names()
            indexes = inspector.get_indexes("users") if "users" in tables else []
            columns = [c["name"] for c in inspector.get_columns("users")] if "users" in tables else []
            return {
                "tables": tables,
                "indexes": indexes,
                "columns": columns,
            }

        inspection = await conn.run_sync(check_tables_and_indexes)

        assert "users" in inspection["tables"]
        assert "id" in inspection["columns"]
        assert "email" in inspection["columns"]
        assert "username" in inspection["columns"]
        assert "password_hash" in inspection["columns"]
        assert "role" in inspection["columns"]
        assert "version" in inspection["columns"]
        assert "permissions" in inspection["columns"]
        assert "nid_number" in inspection["columns"]
        assert "created_at" in inspection["columns"]
        assert "updated_at" in inspection["columns"]

        index_column_names = {col for idx in inspection["indexes"] for col in idx.get("column_names", [])}
        assert "email" in index_column_names
        assert "username" in index_column_names


# ============================================================================
# 2. Bi-directional Migration Reversibility Tests
# ============================================================================


@pytest.mark.asyncio
async def test_migration_bidirectional_reversibility() -> None:
    """Verify that rollback_migration cleanly drops schema and re-upgrade restores it."""
    # Ensure current head is applied
    apply_migrations(revision="head")

    # Roll back by 1 revision (catalog_items table dropped, outbox_events, nid_number, orders, products, version, posts & users remain)
    rollback_migration(revision="-1")

    async with engine.connect() as conn:

        def check_after_rollback_catalog(sync_conn: Connection) -> tuple[list[str], list[str]]:
            insp = inspect(sync_conn)
            tbls = insp.get_table_names()
            cols = [c["name"] for c in insp.get_columns("users")] if "users" in tbls else []
            return tbls, cols

        tables_after_catalog, user_cols_after_catalog = await conn.run_sync(check_after_rollback_catalog)
        assert "catalog_items" not in tables_after_catalog
        assert "outbox_events" in tables_after_catalog
        assert "orders" in tables_after_catalog
        assert "products" in tables_after_catalog
        assert "users" in tables_after_catalog
        assert "posts" in tables_after_catalog
        assert "nid_number" in user_cols_after_catalog

    # Roll back by 1 revision (outbox_events table dropped, nid_number, orders, products, version, posts & users remain)
    rollback_migration(revision="-1")

    async with engine.connect() as conn:

        def check_after_rollback_outbox(sync_conn: Connection) -> tuple[list[str], list[str]]:
            insp = inspect(sync_conn)
            tbls = insp.get_table_names()
            cols = [c["name"] for c in insp.get_columns("users")] if "users" in tbls else []
            return tbls, cols

        tables_after_outbox, user_cols_after_outbox = await conn.run_sync(check_after_rollback_outbox)
        assert "outbox_events" not in tables_after_outbox
        assert "orders" in tables_after_outbox
        assert "products" in tables_after_outbox
        assert "users" in tables_after_outbox
        assert "posts" in tables_after_outbox
        assert "nid_number" in user_cols_after_outbox

    # Roll back by 1 revision (nid_number column dropped from users, orders, products, version, posts & users remain)
    rollback_migration(revision="-1")

    async with engine.connect() as conn:

        def check_after_rollback_nid(sync_conn: Connection) -> tuple[list[str], list[str]]:
            insp = inspect(sync_conn)
            tbls = insp.get_table_names()
            cols = [c["name"] for c in insp.get_columns("users")] if "users" in tbls else []
            return tbls, cols

        tables_after_nid, user_cols_after_nid = await conn.run_sync(check_after_rollback_nid)
        assert "orders" in tables_after_nid
        assert "products" in tables_after_nid
        assert "users" in tables_after_nid
        assert "posts" in tables_after_nid
        assert "version" in user_cols_after_nid
        assert "permissions" in user_cols_after_nid
        assert "nid_number" not in user_cols_after_nid

    # Roll back by 1 revision (orders table dropped, permissions, products, version, posts & users remain)
    rollback_migration(revision="-1")

    # Verify orders table was dropped while permissions, products, users, posts, and version column remain
    async with engine.connect() as conn:

        def check_after_rollback_orders(sync_conn: Connection) -> tuple[list[str], list[str]]:
            insp = inspect(sync_conn)
            tbls = insp.get_table_names()
            cols = [c["name"] for c in insp.get_columns("users")] if "users" in tbls else []
            return tbls, cols

        tables_after_orders, user_cols_after_orders = await conn.run_sync(check_after_rollback_orders)
        assert "orders" not in tables_after_orders
        assert "products" in tables_after_orders
        assert "users" in tables_after_orders
        assert "posts" in tables_after_orders
        assert "version" in user_cols_after_orders
        assert "permissions" in user_cols_after_orders

    # Roll back by another 1 revision (permissions column dropped from users)
    rollback_migration(revision="-1")

    # Verify permissions column was dropped while products, users, posts, and version column remain
    async with engine.connect() as conn:

        def check_after_rollback_permissions(sync_conn: Connection) -> tuple[list[str], list[str]]:
            insp = inspect(sync_conn)
            tbls = insp.get_table_names()
            cols = [c["name"] for c in insp.get_columns("users")] if "users" in tbls else []
            return tbls, cols

        tables_after_1, user_cols_after_1 = await conn.run_sync(check_after_rollback_permissions)
        assert "products" in tables_after_1
        assert "users" in tables_after_1
        assert "posts" in tables_after_1
        assert "version" in user_cols_after_1
        assert "permissions" not in user_cols_after_1

    # Roll back to 2e031b9ce7b1 (products table dropped, version, posts & users remain)
    rollback_migration(revision="2e031b9ce7b1")

    async with engine.connect() as conn:

        def check_after_rollback_products(sync_conn: Connection) -> tuple[list[str], list[str]]:
            insp = inspect(sync_conn)
            tbls = insp.get_table_names()
            cols = [c["name"] for c in insp.get_columns("users")] if "users" in tbls else []
            return tbls, cols

        tables_after_prod, user_cols_after_prod = await conn.run_sync(check_after_rollback_products)
        assert "products" not in tables_after_prod
        assert "users" in tables_after_prod
        assert "posts" in tables_after_prod
        assert "version" in user_cols_after_prod

    # Roll back to 6bd9533b08d1 (version column dropped from users, posts remains)
    rollback_migration(revision="6bd9533b08d1")

    async with engine.connect() as conn:

        def check_after_rollback_version(sync_conn: Connection) -> tuple[list[str], list[str]]:
            insp = inspect(sync_conn)
            tbls = insp.get_table_names()
            cols = [c["name"] for c in insp.get_columns("users")] if "users" in tbls else []
            return tbls, cols

        tables_after_ver, user_cols_after_ver = await conn.run_sync(check_after_rollback_version)
        assert "users" in tables_after_ver
        assert "posts" in tables_after_ver
        assert "version" not in user_cols_after_ver

    # Roll back to e25bf437c78f (posts table dropped, users remains)
    rollback_migration(revision="e25bf437c78f")

    async with engine.connect() as conn:
        tables_after_rollback_posts = await conn.run_sync(lambda sync_conn: inspect(sync_conn).get_table_names())
        assert "posts" not in tables_after_rollback_posts
        assert "users" in tables_after_rollback_posts

    # Roll back to base (all tables dropped)
    rollback_migration(revision="base")

    async with engine.connect() as conn:
        tables_after_rollback_base = await conn.run_sync(lambda sync_conn: inspect(sync_conn).get_table_names())
        assert "users" not in tables_after_rollback_base
        assert "posts" not in tables_after_rollback_base
        assert "products" not in tables_after_rollback_base
        assert "orders" not in tables_after_rollback_base
        assert "outbox_events" not in tables_after_rollback_base
        assert "catalog_items" not in tables_after_rollback_base

    # Re-apply migrations to head
    apply_migrations(revision="head")

    # Verify all tables, version column, permissions, products table, orders, outbox_events, and catalog_items were successfully restored
    async with engine.connect() as conn:

        def check_after_reupgrade(sync_conn: Connection) -> tuple[list[str], list[str]]:
            insp = inspect(sync_conn)
            tbls = insp.get_table_names()
            cols = [c["name"] for c in insp.get_columns("users")] if "users" in tbls else []
            return tbls, cols

        tables_after_reupgrade, user_cols_after_reupgrade = await conn.run_sync(check_after_reupgrade)
        assert "users" in tables_after_reupgrade
        assert "posts" in tables_after_reupgrade
        assert "products" in tables_after_reupgrade
        assert "orders" in tables_after_reupgrade
        assert "outbox_events" in tables_after_reupgrade
        assert "catalog_items" in tables_after_reupgrade
        assert "version" in user_cols_after_reupgrade
        assert "permissions" in user_cols_after_reupgrade
        assert "nid_number" in user_cols_after_reupgrade


# ============================================================================
# 3. Schema Drift Detection Test
# ============================================================================


@pytest.mark.asyncio
async def test_schema_drift_detection_reports_zero_differences() -> None:
    """Verify that Alembic detects zero schema drift between target_metadata and database."""
    # Ensure current schema is at head
    apply_migrations(revision="head")

    async with engine.connect() as conn:

        def check_drift(sync_conn: Connection) -> list[Any]:
            migration_context = MigrationContext.configure(sync_conn)
            # compare_metadata returns any differences between DB schema and declarative Base.metadata
            diff = compare_metadata(migration_context, Base.metadata)
            return list(diff)

        diffs = await conn.run_sync(check_drift)
        # An empty diff list proves zero schema drift
        assert diffs == [], f"Detected unexpected schema drifts: {diffs}"


# ============================================================================
# 4. UserModel ORM Entity Persistence Test
# ============================================================================


@pytest.mark.asyncio
async def test_user_model_persistence_and_timestamps() -> None:
    """Verify that UserModel instances persist, query, and populate timestamps cleanly."""
    apply_migrations(revision="head")

    test_email = "alembic.architect@example.com"
    test_username = "alembic_architect"

    async with async_session_factory() as session:
        # Clean up any existing entity with the same email/username
        existing = await session.scalar(select(UserModel).where(UserModel.email == test_email))
        if existing:
            await session.delete(existing)
            await session.commit()

        user = UserModel(
            email=test_email,
            username=test_username,
            password_hash="pbkdf2_hashed_secret_val",
            full_name="Alembic Master",
            role="admin",
            company_name="Enterprise Arc",
        )
        session.add(user)
        await session.commit()

        # Invariant check: expire_on_commit=False guarantees attribute access post-commit
        assert user.id is not None
        assert user.created_at is not None
        assert user.updated_at is not None
        assert user.email == test_email
        assert user.role == "admin"
        assert user.is_active is True

    # Retrieve in a distinct session to assert database round-trip fidelity
    async with async_session_factory() as verify_session:
        retrieved = await verify_session.scalar(select(UserModel).where(UserModel.email == test_email))
        assert retrieved is not None
        assert retrieved.username == test_username
        assert retrieved.full_name == "Alembic Master"
        assert retrieved.company_name == "Enterprise Arc"

        # Cleanup
        await verify_session.delete(retrieved)
        await verify_session.commit()
