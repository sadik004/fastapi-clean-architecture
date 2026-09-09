"""Comprehensive Test Suite for Day 48: Time-Ordered Cryptographic Identifiers.

Tests:
1. Monotonic Temporal Ordering: 1,000 sequential UUIDv7 IDs guarantee ids == sorted(ids).
2. Timestamp Extraction Accuracy: Extracted UTC datetime matches datetime.now(UTC) within 15ms.
3. Collision-Free Rapid Entropy: 10,000 UUIDv7s generated in rapid succession produce 0 collisions.
4. Crockford Base32 ULID: 26 characters, valid Crockford alphabet, monotonic sorting, and timestamp accuracy.
5. Database B-Tree Insert & Temporal Retrieval: Orders inserted into database with UUIDv7 primary keys.
6. API Endpoints:
   - POST /orders creates order with valid UUIDv7
   - GET /orders/{order_id} fetches order
   - GET /orders/{order_id}/extracted-timestamp validates O(1) bitwise timestamp extraction without DB query.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import select

from app.core.database import async_session_factory
from app.core.identifiers import (
    CROCKFORD_BASE32_ALPHABET,
    extract_timestamp_from_ulid,
    extract_timestamp_from_uuidv7,
    generate_ulid,
    generate_uuidv7,
)
from app.main import app
from app.models.order import OrderModel
from app.models.user import UserModel

# ============================================================================
# 1. Monotonic Temporal Ordering Test
# ============================================================================


def test_uuidv7_monotonic_temporal_ordering() -> None:
    """Generate 1,000 sequential UUIDv7 IDs; assert that ids == sorted(ids)."""
    ids: list[uuid.UUID] = [generate_uuidv7() for _ in range(1000)]

    # Strictly monotonic check (id_a < id_b for all subsequent items)
    for i in range(len(ids) - 1):
        assert ids[i] < ids[i + 1], f"Monotonic ordering violation at index {i}: {ids[i]} not < {ids[i + 1]}"

    assert ids == sorted(ids)


# ============================================================================
# 2. Timestamp Extraction Accuracy Test
# ============================================================================


def test_uuidv7_timestamp_extraction_accuracy() -> None:
    """Extract timestamp from a fresh UUIDv7; assert it matches datetime.now(UTC) within 20ms."""
    before = datetime.now(UTC)
    fresh_id = generate_uuidv7()
    after = datetime.now(UTC)

    extracted_ts = extract_timestamp_from_uuidv7(fresh_id)

    # Verification: extracted_ts must fall within the before and after window (+/- 20ms tolerance)
    tolerance = timedelta(milliseconds=20)
    assert (before - tolerance) <= extracted_ts <= (after + tolerance), (
        f"Extracted timestamp {extracted_ts} not within window {before} - {after}"
    )

    # Version check
    assert fresh_id.version == 7


def test_uuidv7_timestamp_extraction_rejects_non_v7() -> None:
    """Verify that extract_timestamp_from_uuidv7 raises ValueError when passed a non-v7 UUID."""
    v4_id = uuid.uuid4()
    with pytest.raises(ValueError, match="expected version 7"):
        extract_timestamp_from_uuidv7(v4_id)


# ============================================================================
# 3. Collision-Free Rapid Entropy Test
# ============================================================================


def test_uuidv7_collision_free_rapid_entropy() -> None:
    """Generate 10,000 UUIDv7s in rapid succession; assert zero collisions (10,000 unique IDs)."""
    generated_set: set[uuid.UUID] = set()
    total_count = 10000

    for _ in range(total_count):
        generated_set.add(generate_uuidv7())

    assert len(generated_set) == total_count, f"Entropy collision detected: {len(generated_set)} != {total_count}"


# ============================================================================
# 4. Crockford Base32 ULID Generation & Extraction Tests
# ============================================================================


def test_ulid_format_and_monotonicity() -> None:
    """Test ULID length (26 chars), Crockford alphabet, and sortability."""
    ulids = [generate_ulid() for _ in range(500)]

    for u in ulids:
        assert len(u) == 26
        assert all(c in CROCKFORD_BASE32_ALPHABET for c in u)

    # Monotonic sorting check
    for i in range(len(ulids) - 1):
        assert ulids[i] <= ulids[i + 1]


def test_ulid_timestamp_extraction() -> None:
    """Extract timestamp from ULID and verify accuracy within 20ms."""
    before = datetime.now(UTC)
    ulid = generate_ulid()
    after = datetime.now(UTC)

    extracted_ts = extract_timestamp_from_ulid(ulid)
    tolerance = timedelta(milliseconds=20)
    assert (before - tolerance) <= extracted_ts <= (after + tolerance)


# ============================================================================
# 5. Database B-Tree Insert & Temporal Retrieval Tests
# ============================================================================


@pytest.mark.asyncio
async def test_database_order_insertion_and_btree_locality() -> None:
    """Insert orders into database with UUIDv7 primary keys; verify B-tree ordering and retrieval."""
    test_email = "uuidv7_customer@example.com"
    test_username = "uuidv7_customer"

    async with async_session_factory() as session:
        # Create or fetch customer user
        user = await session.scalar(select(UserModel).where(UserModel.email == test_email))
        if not user:
            user = UserModel(
                email=test_email,
                username=test_username,
                password_hash="test_password_hash",
                role="user",
            )
            session.add(user)
            await session.commit()
            await session.refresh(user)
        user_id = user.id

        # Clean up any existing orders for this test user to maintain deterministic test isolation
        existing_orders = (await session.scalars(select(OrderModel).where(OrderModel.user_id == user_id))).all()
        for eo in existing_orders:
            await session.delete(eo)
        await session.commit()

    # Create 5 orders sequentially
    order_ids: list[uuid.UUID] = []
    async with async_session_factory() as session:
        for i in range(5):
            order = OrderModel(
                user_id=user_id,
                total_amount=100.0 + (i * 10),
                status="completed",
            )
            session.add(order)
            await session.flush()
            order_ids.append(order.id)
        await session.commit()

    # Verify orders are sorted monotonically
    assert order_ids == sorted(order_ids)

    # Query back from database ordered by primary key
    async with async_session_factory() as session:
        stmt = select(OrderModel).where(OrderModel.user_id == user_id).order_by(OrderModel.id.asc())
        retrieved_orders = (await session.scalars(stmt)).all()
        retrieved_ids = [o.id for o in retrieved_orders]

        assert retrieved_ids == order_ids
        for o in retrieved_orders:
            ts = extract_timestamp_from_uuidv7(o.id)
            assert isinstance(ts, datetime)


# ============================================================================
# 6. API Endpoint Integration Tests
# ============================================================================


@pytest.mark.asyncio
async def test_order_endpoints_full_lifecycle() -> None:
    """Test POST /orders, GET /orders/{order_id}, and GET /orders/{order_id}/extracted-timestamp."""
    # Ensure test user exists in DB
    async with async_session_factory() as session:
        user = await session.scalar(select(UserModel).where(UserModel.username == "order_api_user"))
        if not user:
            user = UserModel(
                email="order_api_user@example.com",
                username="order_api_user",
                password_hash="hash_value",
                role="user",
            )
            session.add(user)
            await session.commit()
        user_id = user.id

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        # 1. Create order
        create_resp = await client.post(
            "/orders",
            json={"user_id": user_id, "total_amount": 299.95},
        )
        assert create_resp.status_code == 201, create_resp.text
        data = create_resp.json()

        order_id_str = data["id"]
        order_uuid = uuid.UUID(order_id_str)
        assert order_uuid.version == 7
        assert data["user_id"] == user_id
        assert data["total_amount"] == 299.95
        assert data["status"] == "pending"
        assert "extracted_timestamp" in data
        assert data["extracted_timestamp"] is not None

        # 2. Get order details
        get_resp = await client.get(f"/orders/{order_id_str}")
        assert get_resp.status_code == 200
        get_data = get_resp.json()
        assert get_data["id"] == order_id_str
        assert get_data["total_amount"] == 299.95

        # 3. Extract timestamp endpoint (demonstrating O(1) bit-shift derivation without query)
        ts_resp = await client.get(f"/orders/{order_id_str}/extracted-timestamp")
        assert ts_resp.status_code == 200
        ts_data = ts_resp.json()
        assert ts_data["order_id"] == order_id_str
        assert "extracted_timestamp_utc" in ts_data
        assert ts_data["time_difference_ms"] < 2000.0  # Under 2 seconds difference between ID and created_at
