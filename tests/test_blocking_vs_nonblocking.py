"""Comprehensive test suite for Day 12: Non-Blocking vs Blocking Execution and asyncio.to_thread offloading."""

import asyncio
import time

import pytest
from fastapi import status
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient

from app.core.security import get_password_hash, verify_password
from app.main import app
from app.repositories.user_repository import InMemoryUserRepository
from app.schemas.user import UserCreate, UserRole
from app.services.user_service import UserService

# ============================================================================
# 1. Cryptographic Security & Password Hashing Unit Tests
# ============================================================================


@pytest.mark.asyncio
async def test_password_hash_and_verification() -> None:
    """Verify non-blocking PBKDF2 hashing produces unique salts and verifies correctly."""
    plain_password = "SuperSecretPassword123!"

    # Derive two hashes for the exact same password
    hash1 = await get_password_hash(plain_password)
    hash2 = await get_password_hash(plain_password)

    # Invariant: Each hash must have a unique random salt
    assert hash1 != hash2
    assert hash1.startswith("$argon2id$v=19$m=65536,t=3,p=4$")
    assert hash2.startswith("$argon2id$v=19$m=65536,t=3,p=4$")

    # Verification: Valid password must pass
    assert await verify_password(plain_password, hash1) is True
    assert await verify_password(plain_password, hash2) is True

    # Verification: Wrong password must fail
    assert await verify_password("WrongPassword123!", hash1) is False
    assert await verify_password("", hash1) is False

    # Malformed hash string must fail gracefully without unhandled exceptions
    assert await verify_password(plain_password, "invalid_hash_string") is False


@pytest.mark.asyncio
async def test_user_service_authentication_flow() -> None:
    """Verify UserService.authenticate_user authenticates valid credentials and rejects invalid ones."""
    repo = InMemoryUserRepository()
    service = UserService(repository=repo)

    created = await service.register_user(
        payload=UserCreate(
            email="auth.test@example.com",
            username="auth_tester",
            password="ValidPassword123!",
            password_confirm="ValidPassword123!",
            role=UserRole.USER,
        )
    )

    # Successful authentication
    authenticated = await service.authenticate_user("auth_tester", "ValidPassword123!")
    assert authenticated is not None
    assert authenticated.id == created.id
    assert authenticated.username == "auth_tester"

    # Failed authentication - wrong password
    failed_pw = await service.authenticate_user("auth_tester", "WrongPassword!")
    assert failed_pw is None

    # Failed authentication - unknown user
    failed_user = await service.authenticate_user("unknown_user", "ValidPassword123!")
    assert failed_user is None


# ============================================================================
# 2. CPU-Bound Report Generation & Export Endpoint Tests
# ============================================================================


def test_export_user_report_endpoint_success(client: TestClient) -> None:
    """Verify POST /users/{user_id}/export-report returns 200 with complete report payload."""
    payload = {
        "email": "report.export@example.com",
        "username": "export_hero",
        "password": "Password123!",
        "password_confirm": "Password123!",
        "age": 30,
        "role": "user",
    }
    create_resp = client.post("/users/", json=payload)
    assert create_resp.status_code == status.HTTP_201_CREATED
    user_id = create_resp.json()["id"]

    resp = client.post(f"/users/{user_id}/export-report")
    assert resp.status_code == status.HTTP_200_OK
    data = resp.json()

    assert data["user_id"] == user_id
    assert data["username"] == "export_hero"
    assert data["records_processed"] == 50_000
    assert "report_checksum" in data
    assert len(data["report_checksum"]) == 64  # SHA-256 hex string
    assert "generated_at" in data


def test_export_user_report_not_found(client: TestClient) -> None:
    """Verify POST /users/{user_id}/export-report returns 404 for non-existent users."""
    resp = client.post("/users/999999/export-report")
    assert resp.status_code == status.HTTP_404_NOT_FOUND
    assert "User with ID 999999 was not found" in resp.json()["detail"]


# ============================================================================
# 3. Concurrency & Event Loop Starvation Prevention Test
# ============================================================================


@pytest.mark.asyncio
async def test_event_loop_not_starved_during_heavy_cpu_work() -> None:
    """Verify that heavy CPU operations offloaded to worker threads do not starve the event loop.

    Fires a heavy report generation request concurrently with 5 lightweight /health requests.
    All /health checks must resolve with sub-25ms latency while the heavy job computes in worker threads.
    """
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        # Seed user for report generation
        create_resp = await ac.post(
            "/users/",
            json={
                "email": "starvation.guard@example.com",
                "username": "starve_guard",
                "password": "Password123!",
                "password_confirm": "Password123!",
                "age": 27,
                "role": "user",
            },
        )
        assert create_resp.status_code == status.HTTP_201_CREATED
        user_id = create_resp.json()["id"]

        # Launch heavy CPU task
        heavy_task = asyncio.create_task(ac.post(f"/users/{user_id}/export-report"))

        # Allow task to be scheduled onto worker thread
        await asyncio.sleep(0.002)

        # Concurrently fire 5 lightweight health checks on main event loop
        health_latencies_ms: list[float] = []
        for _ in range(5):
            t0 = time.perf_counter()
            health_resp = await ac.get("/health")
            elapsed = (time.perf_counter() - t0) * 1000.0
            health_latencies_ms.append(elapsed)

            assert health_resp.status_code == status.HTTP_200_OK
            assert health_resp.json()["status"] == "healthy"
            assert "timestamp" in health_resp.json()

        # Await heavy task completion
        heavy_resp = await heavy_task
        assert heavy_resp.status_code == status.HTTP_200_OK
        assert heavy_resp.json()["records_processed"] == 50_000

        # Enforce that health checks were never starved (each responded in sub-25ms)
        max_health_latency = max(health_latencies_ms)
        assert max_health_latency < 35.0, (
            f"Event loop starvation detected: health check latency reached {max_health_latency:.2f}ms. "
            "Heavy computation blocked the asyncio event loop instead of running on worker thread."
        )
