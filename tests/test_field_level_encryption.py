"""Comprehensive test suite for Day 49: Application-Level Field-Level Encryption (FLE) Architecture.

Verifies:
1. FernetEngine Direct Cryptography: Authenticated symmetric encryption (AES-128-CBC + HMAC-SHA256)
   produces valid Fernet tokens (starting with 'gAAAAA') and decrypts faithfully.
2. Cryptographic Tamper Detection: Modifying ciphertext bytes raises EncryptionTamperingException.
3. Transparent ORM Round-Trip: Inserting UserModel with plain nid_number decrypts transparently on SELECT.
4. Raw SQL Storage Verification: Direct SQL inspection confirms database stores ONLY authenticated ciphertext
   and never leaks plaintext PII.
5. Database Ciphertext Tampering Defense: Mutating stored ciphertext in SQLite causes ORM fetch to raise
   EncryptionTamperingException.
6. Nullable FLE Handling: Users with nid_number=None persist and retrieve cleanly.
7. Repository & Service Layer: Both SqlAlchemyUserRepository and InMemoryUserRepository support
   transparent NID operations.
8. HTTP Endpoint Verification: PUT /api/v1/users/{user_id}/nid updates NID, returning decrypted plaintext
   in UserResponse while storing ciphertext in the database.
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from app.core.database import async_session_factory
from app.core.encryption import get_fernet_engine
from app.core.exceptions import EncryptionTamperingException, UserNotFoundException
from app.main import app
from app.models.user import UserModel
from app.repositories.sqlalchemy_user_repository import SqlAlchemyUserRepository
from app.repositories.user_repository import InMemoryUserRepository
from app.services.user_service import UserService

# ============================================================================
# 1. FernetEngine Direct Unit Tests
# ============================================================================


def test_fernet_engine_encryption_roundtrip() -> None:
    """Verify FernetEngine encrypts plaintext to token starting with 'gAAAAA' and decrypts faithfully."""
    engine = get_fernet_engine()
    plaintext = "19951234567890123"
    ciphertext = engine.encrypt(plaintext)

    assert ciphertext.startswith("gAAAAA")
    assert plaintext not in ciphertext

    decrypted = engine.decrypt(ciphertext)
    assert decrypted == plaintext


def test_fernet_engine_tampering_detection() -> None:
    """Verify modifying ciphertext raises EncryptionTamperingException due to HMAC verification failure."""
    engine = get_fernet_engine()
    plaintext = "top_secret_national_identity"
    ciphertext = engine.encrypt(plaintext)

    # Mutate a character in the middle of ciphertext (tampering payload)
    char_list = list(ciphertext)
    target_idx = len(ciphertext) // 2
    char_list[target_idx] = "X" if char_list[target_idx] != "X" else "Y"
    tampered_ciphertext = "".join(char_list)

    with pytest.raises(EncryptionTamperingException):
        engine.decrypt(tampered_ciphertext)


def test_fernet_engine_invalid_token() -> None:
    """Verify arbitrary garbage string raises EncryptionTamperingException."""
    engine = get_fernet_engine()
    with pytest.raises(EncryptionTamperingException):
        engine.decrypt("not_a_valid_fernet_token_at_all")


# ============================================================================
# 2. Transparent ORM & Raw SQL Verification Tests
# ============================================================================


@pytest.mark.asyncio
async def test_orm_transparent_round_trip_encryption() -> None:
    """Verify transparent ORM round-trip: plaintext inserted is automatically decrypted on SELECT."""
    plain_nid = "NID-1995-88442211"
    test_email = "fle_orm_roundtrip@example.com"
    test_username = "fle_orm_user"

    async with async_session_factory() as session:
        user = UserModel(
            email=test_email,
            username=test_username,
            password_hash="hashed_pw_secret",
            full_name="FLE Citizen",
            nid_number=plain_nid,
        )
        session.add(user)
        await session.commit()
        user_id = user.id

    # In a brand new session, fetch the user via ORM
    async with async_session_factory() as session:
        retrieved = await session.get(UserModel, user_id)
        assert retrieved is not None
        assert retrieved.nid_number == plain_nid

        # Cleanup
        await session.delete(retrieved)
        await session.commit()


@pytest.mark.asyncio
async def test_raw_sql_storage_ciphertext_verification() -> None:
    """Verify via direct raw SQL that database stores ONLY ciphertext and plaintext NEVER appears in SQLite."""
    plain_nid = "NID-2026-CONFIDENTIAL-999"
    test_email = "fle_raw_sql@example.com"
    test_username = "fle_raw_sql_user"

    async with async_session_factory() as session:
        user = UserModel(
            email=test_email,
            username=test_username,
            password_hash="hashed_pw_secret",
            full_name="Confidential Citizen",
            nid_number=plain_nid,
        )
        session.add(user)
        await session.commit()
        user_id = user.id

    # Execute raw SQL query bypassing SQLAlchemy TypeDecorator
    async with async_session_factory() as session:
        raw_result = await session.execute(
            text("SELECT nid_number FROM users WHERE id = :user_id"),
            {"user_id": user_id},
        )
        raw_nid = raw_result.scalar_one()

        # Invariants:
        # 1. Must be Fernet token starting with 'gAAAAA'
        assert raw_nid.startswith("gAAAAA")
        # 2. Must strictly NOT contain the plaintext
        assert plain_nid not in raw_nid
        # 3. Decrypting raw_nid with engine must yield original plaintext
        engine = get_fernet_engine()
        assert engine.decrypt(raw_nid) == plain_nid

        # Cleanup
        user_to_delete = await session.get(UserModel, user_id)
        if user_to_delete:
            await session.delete(user_to_delete)
            await session.commit()


@pytest.mark.asyncio
async def test_database_ciphertext_tampering_detection() -> None:
    """Verify mutating database ciphertext directly causes ORM query to raise EncryptionTamperingException."""
    plain_nid = "NID-TAMPER-TEST-777"
    test_email = "fle_tamper@example.com"
    test_username = "fle_tamper_user"

    async with async_session_factory() as session:
        user = UserModel(
            email=test_email,
            username=test_username,
            password_hash="hashed_pw_secret",
            full_name="Tamper Guard Citizen",
            nid_number=plain_nid,
        )
        session.add(user)
        await session.commit()
        user_id = user.id

    # Directly mutate raw ciphertext in SQLite
    async with async_session_factory() as session:
        raw_result = await session.execute(
            text("SELECT nid_number FROM users WHERE id = :user_id"),
            {"user_id": user_id},
        )
        original_ciphertext = raw_result.scalar_one()

        # Mutate single character
        mutated_ciphertext = (
            original_ciphertext[:20] + ("Z" if original_ciphertext[20] != "Z" else "A") + original_ciphertext[21:]
        )

        await session.execute(
            text("UPDATE users SET nid_number = :mutated WHERE id = :user_id"),
            {"mutated": mutated_ciphertext, "user_id": user_id},
        )
        await session.commit()

    # Attempting to load user via ORM should trigger HMAC verification failure
    async with async_session_factory() as session:
        with pytest.raises(EncryptionTamperingException):
            await session.get(UserModel, user_id)

    # Cleanup with raw SQL
    async with async_session_factory() as session:
        await session.execute(
            text("DELETE FROM users WHERE id = :user_id"),
            {"user_id": user_id},
        )
        await session.commit()


@pytest.mark.asyncio
async def test_nullable_nid_number_handling() -> None:
    """Verify users without NID (nid_number=None) persist and query cleanly."""
    test_email = "fle_null_nid@example.com"
    test_username = "fle_null_nid_user"

    async with async_session_factory() as session:
        user = UserModel(
            email=test_email,
            username=test_username,
            password_hash="hashed_pw_secret",
            full_name="No NID Citizen",
            nid_number=None,
        )
        session.add(user)
        await session.commit()
        user_id = user.id

    async with async_session_factory() as session:
        retrieved = await session.get(UserModel, user_id)
        assert retrieved is not None
        assert retrieved.nid_number is None

        # Direct SQL inspection
        raw_result = await session.execute(
            text("SELECT nid_number FROM users WHERE id = :user_id"),
            {"user_id": user_id},
        )
        assert raw_result.scalar_one() is None

        # Cleanup
        await session.delete(retrieved)
        await session.commit()


# ============================================================================
# 3. Repository & Service Layer Tests
# ============================================================================


@pytest.mark.asyncio
async def test_sqlalchemy_user_repository_nid_operations() -> None:
    """Verify SqlAlchemyUserRepository create and update_nid operations."""
    async with async_session_factory() as session:
        repo = SqlAlchemyUserRepository(session=session)
        created = await repo.create(
            email="fle_repo_user@example.com",
            username="fle_repo_user",
            password_hash="secret_hash",
            nid_number="NID-ORIGINAL-12345",
        )
        await session.commit()
        user_id = created.id
        assert created.nid_number == "NID-ORIGINAL-12345"

    async with async_session_factory() as session:
        repo = SqlAlchemyUserRepository(session=session)
        updated = await repo.update_nid(user_id, "NID-UPDATED-67890")
        await session.commit()
        assert updated is not None
        assert updated.nid_number == "NID-UPDATED-67890"

        # Cleanup
        user_model = await session.get(UserModel, user_id)
        if user_model:
            await session.delete(user_model)
            await session.commit()


@pytest.mark.asyncio
async def test_in_memory_user_repository_nid_operations() -> None:
    """Verify InMemoryUserRepository create and update_nid operations."""
    repo = InMemoryUserRepository()
    user = await repo.create(
        email="mem_nid@example.com",
        username="mem_nid_user",
        password_hash="hash",
        nid_number="MEM-NID-1111",
    )
    assert user.nid_number == "MEM-NID-1111"

    updated = await repo.update_nid(user.id, "MEM-NID-2222")
    assert updated is not None
    assert updated.nid_number == "MEM-NID-2222"


@pytest.mark.asyncio
async def test_user_service_update_nid_not_found() -> None:
    """Verify UserService.update_nid raises UserNotFoundException for non-existent ID."""
    repo = InMemoryUserRepository()
    service = UserService(repository=repo)
    with pytest.raises(UserNotFoundException):
        await service.update_nid(user_id=99999, nid_number="12345")


# ============================================================================
# 4. HTTP Endpoint Integration Tests
# ============================================================================


@pytest.mark.asyncio
async def test_api_endpoint_update_user_nid() -> None:
    """Verify PUT /users/{user_id}/nid updates NID and returns decrypted plaintext."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Register user
        create_payload = {
            "email": "fle_endpoint_user@example.com",
            "username": "fle_endpoint_user",
            "password": "Password123!",
            "password_confirm": "Password123!",
            "full_name": "FLE Endpoint Citizen",
        }
        res_create = await client.post("/users/", json=create_payload)
        assert res_create.status_code == 201, res_create.text
        user_data = res_create.json()
        user_id = user_data["id"]

        # Initially nid_number is None
        assert user_data.get("nid_number") is None

        # Update NID via PUT /users/{user_id}/nid
        nid_to_set = "88990011223344"
        res_nid = await client.put(
            f"/users/{user_id}/nid",
            json={"nid_number": nid_to_set},
        )
        assert res_nid.status_code == 200, res_nid.text
        updated_data = res_nid.json()
        assert updated_data["nid_number"] == nid_to_set

        # HTTP contract: response must contain decrypted plaintext
        assert updated_data["nid_number"] == nid_to_set

        # Cleanup: remove test user directly from the database
        async with async_session_factory() as session:
            user_model = await session.get(UserModel, user_id)
            if user_model:
                await session.delete(user_model)
                await session.commit()


@pytest.mark.asyncio
async def test_api_endpoint_update_user_nid_not_found() -> None:
    """Verify PUT /users/{user_id}/nid returns HTTP 404 with USER_NOT_FOUND code for invalid user ID."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.put(
            "/users/999999/nid",
            json={"nid_number": "123456789"},
        )
        assert res.status_code == 404
        assert res.json()["error"]["code"] == "USER_NOT_FOUND"
