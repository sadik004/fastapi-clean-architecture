"""Comprehensive test suite for Day 43: Cryptographic Password Security with Argon2id & Asyncio Event Loop Offloading."""

import asyncio
import time

import pytest
from argon2 import PasswordHasher
from httpx import ASGITransport, AsyncClient

from app.core.security import (
    hash_password,
    hash_password_async,
    needs_rehash,
    needs_rehash_async,
    verify_password_async,
    verify_password_sync,
)
from app.main import app
from app.repositories.user_repository import InMemoryUserRepository
from app.schemas.user import UserCreate, UserRole
from app.services.user_service import UserService


@pytest.mark.asyncio
async def test_argon2id_hash_and_verify_correctness() -> None:
    """Verify Argon2id hashing generates valid hashes and verifies correctly."""
    plain_password = "SecureEnterprisePassword2026!"

    hashed = await hash_password_async(plain_password)
    assert hashed.startswith("$argon2id$v=19$m=65536,t=3,p=4$")

    # Correct password verification
    assert await verify_password_async(plain_password, hashed) is True
    assert verify_password_sync(plain_password, hashed) is True

    # Incorrect password verification
    assert await verify_password_async("IncorrectPassword123!", hashed) is False
    assert await verify_password_async("", hashed) is False

    # Malformed hash strings should fail gracefully
    assert await verify_password_async(plain_password, "invalid_corrupted_hash") is False
    assert await verify_password_async(plain_password, "") is False


@pytest.mark.asyncio
async def test_argon2id_unique_salt_invariant() -> None:
    """Verify that hashing identical passwords twice produces distinct cryptographic salts."""
    plain = "IdenticalPasswordXYZ!"

    hash1 = await hash_password_async(plain)
    hash2 = await hash_password_async(plain)

    # Invariant: Salts must be cryptographically distinct to defeat rainbow table attacks
    assert hash1 != hash2
    assert hash1.startswith("$argon2id$v=19$m=65536,t=3,p=4$")
    assert hash2.startswith("$argon2id$v=19$m=65536,t=3,p=4$")

    # Both must independently verify against the plain password
    assert await verify_password_async(plain, hash1) is True
    assert await verify_password_async(plain, hash2) is True


@pytest.mark.asyncio
async def test_event_loop_non_blocking_during_hashing() -> None:
    """Verify asyncio.to_thread offloading prevents event loop starvation during Argon2id execution."""
    transport = ASGITransport(app=app)

    async def _heavy_hashing_worker() -> None:
        for _ in range(3):
            await hash_password_async("HeavyLoadPasswordToOffload!")

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Launch heavy Argon2id background tasks
        heavy_task = asyncio.create_task(_heavy_hashing_worker())

        # Yield event loop so background workers start
        await asyncio.sleep(0.01)

        # Health probe must respond in sub-50ms despite concurrent heavy Argon2id hashing
        start_probe = time.perf_counter()
        probe_response = await client.get("/health")
        probe_duration_ms = (time.perf_counter() - start_probe) * 1000

        assert probe_response.status_code == 200
        assert probe_response.json()["status"] == "healthy"
        # If execution were blocking the event loop, probe_duration_ms would exceed 200ms+
        assert probe_duration_ms < 100.0

        await heavy_task


@pytest.mark.asyncio
async def test_automatic_rehash_detection() -> None:
    """Verify needs_rehash identifies legacy algorithms and lower-work-factor hashes."""
    # 1. Fresh OWASP Argon2id hash requires no rehash
    current_hash = hash_password("MyCurrentPassword123!")
    assert needs_rehash(current_hash) is False
    assert await needs_rehash_async(current_hash) is False

    # 2. Legacy PBKDF2 hash requires rehash
    legacy_pbkdf2 = "pbkdf2:sha256:100000$abcd1234$ef012345"
    assert needs_rehash(legacy_pbkdf2) is True
    assert await needs_rehash_async(legacy_pbkdf2) is True

    # 3. Legacy Bcrypt hash requires rehash
    legacy_bcrypt = "$2b$12$feheCpAaov.jc4XbouSEq.LMVr/z1ama9PdTSqlPbKvokKC1atGLK"
    assert needs_rehash(legacy_bcrypt) is True

    # 4. Under-configured Argon2id hash (lower memory/iterations) requires rehash
    low_cost_hasher = PasswordHasher(time_cost=1, memory_cost=8192, parallelism=1)
    low_cost_hash = low_cost_hasher.hash("MyCurrentPassword123!")
    assert needs_rehash(low_cost_hash) is True


@pytest.mark.asyncio
async def test_user_service_transparent_rehash_on_login() -> None:
    """Verify that authenticating with valid credentials transparently upgrades legacy hashes to Argon2id."""
    repo = InMemoryUserRepository()
    service = UserService(repository=repo)

    # 1. Register a user initially with standard registration
    plain_password = "TransparentUpgradePass2026!"
    user = await service.register_user(
        payload=UserCreate(
            email="rehash.user@example.com",
            username="rehash_tester",
            password=plain_password,
            password_confirm=plain_password,
            role=UserRole.USER,
        )
    )
    assert user.password_hash.startswith("$argon2id$v=19$m=65536,t=3,p=4$")

    # 2. Manually downgrade the user's password_hash to a lower-cost legacy hash to simulate legacy migration
    low_cost_hasher = PasswordHasher(time_cost=1, memory_cost=8192, parallelism=1)
    low_cost_hash = low_cost_hasher.hash(plain_password)
    user.password_hash = low_cost_hash
    await repo.update(user.id, password_hash=low_cost_hash)

    # Confirm repository holds the legacy hash
    stored_before = await repo.get_by_id(user.id)
    assert stored_before is not None
    assert stored_before.password_hash == low_cost_hash
    assert needs_rehash(stored_before.password_hash) is True

    # 3. Authenticate with valid password -> triggers seamless automatic rehash
    authenticated = await service.authenticate_user("rehash_tester", plain_password)
    assert authenticated is not None
    assert authenticated.id == user.id

    # 4. Verify user's stored hash in the repository is now upgraded to OWASP Argon2id
    stored_after = await repo.get_by_id(user.id)
    assert stored_after is not None
    assert stored_after.password_hash.startswith("$argon2id$v=19$m=65536,t=3,p=4$")
    assert needs_rehash(stored_after.password_hash) is False

    # 5. Subsequent authentication with upgraded hash does not change the hash again
    authenticated_again = await service.authenticate_user("rehash_tester", plain_password)
    assert authenticated_again is not None
    assert authenticated_again.password_hash == stored_after.password_hash
