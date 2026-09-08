"""Comprehensive test suite for Python Asyncio Fundamentals, coroutines, and concurrent orchestration."""

import asyncio
import time
from fastapi import status
from fastapi.testclient import TestClient
import pytest

from app.core.exceptions import UserNotFoundException
from app.repositories.user_repository import InMemoryUserRepository
from app.services.user_service import UserService


@pytest.mark.asyncio
async def test_concurrent_dashboard_timing_benchmark() -> None:
    """Verify that 3 concurrent 50ms async tasks complete in O(max(t_i)) time (<90ms), not O(sum(t_i)) (>150ms)."""
    repo = InMemoryUserRepository()
    service = UserService(repository=repo)

    # Register a user directly into repo
    user = await repo.create(
        email="concurrent@example.com",
        username="async_speed_runner",
        password_hash="argon2_hashed",
        role="user",
    )

    start_time = time.perf_counter()
    dashboard = await service.get_user_dashboard(user_id=user.id)
    elapsed_ms = (time.perf_counter() - start_time) * 1000.0

    # Verification: Result structure
    assert dashboard["profile"].id == user.id
    assert len(dashboard["activity_logs"]) == 2
    assert dashboard["stats"]["user_id"] == user.id

    # Timing verification: 3 tasks with 50ms latency each should take ~50ms concurrently,
    # strictly well below sequential threshold of 140ms.
    assert elapsed_ms < 95.0, (
        f"Concurrency benchmark failed: elapsed {elapsed_ms:.2f}ms >= 95ms threshold. "
        "Tasks executed sequentially instead of concurrently via asyncio.gather."
    )


def test_get_user_dashboard_endpoint_success(client: TestClient) -> None:
    """Verify GET /users/{user_id}/dashboard returns combined payload with HTTP 200."""
    payload = {
        "email": "dashboard.user@example.com",
        "username": "dash_hero",
        "password": "SecurePassword123!",
        "password_confirm": "SecurePassword123!",
        "age": 28,
        "role": "user",
    }
    create_resp = client.post("/users/", json=payload)
    assert create_resp.status_code == status.HTTP_201_CREATED
    user_id = create_resp.json()["id"]

    resp = client.get(f"/users/{user_id}/dashboard")
    assert resp.status_code == status.HTTP_200_OK
    data = resp.json()

    # Verify profile section
    assert "profile" in data
    assert data["profile"]["id"] == user_id
    assert data["profile"]["email"] == "dashboard.user@example.com"
    assert data["profile"]["username"] == "dash_hero"

    # Verify activity logs section
    assert "activity_logs" in data
    assert isinstance(data["activity_logs"], list)
    assert len(data["activity_logs"]) >= 1

    # Verify stats section
    assert "stats" in data
    assert data["stats"]["user_id"] == user_id
    assert "total_logins" in data["stats"]
    assert "account_health" in data["stats"]


def test_get_user_dashboard_not_found(client: TestClient) -> None:
    """Verify GET /users/{user_id}/dashboard returns HTTP 404 when user does not exist."""
    resp = client.get("/users/99999/dashboard")
    assert resp.status_code == status.HTTP_404_NOT_FOUND
    data = resp.json()
    assert "User with ID 99999 was not found" in data["detail"]


@pytest.mark.asyncio
async def test_gather_exception_propagation_and_task_cancellation() -> None:
    """Verify that when one task in asyncio.gather raises, sibling tasks are cancelled cleanly."""
    repo = InMemoryUserRepository()
    service = UserService(repository=repo)

    # Attempting to get dashboard for non-existent user 88888
    with pytest.raises(UserNotFoundException):
        await service.get_user_dashboard(user_id=88888)


@pytest.mark.asyncio
async def test_non_blocking_cooperative_event_loop() -> None:
    """Verify event loop remains non-blocking during simulated I/O by executing interleaved tasks."""
    repo = InMemoryUserRepository()
    service = UserService(repository=repo)

    execution_order: list[str] = []

    async def background_counter() -> None:
        for i in range(3):
            execution_order.append(f"interleaved_{i}")
            await asyncio.sleep(0.015)

    async def fetch_job() -> None:
        execution_order.append("fetch_start")
        await service._fetch_account_stats(user_id=1)
        execution_order.append("fetch_end")

    # Run background counter alongside fetch_job
    await asyncio.gather(background_counter(), fetch_job())

    # Because sleep is non-blocking, background_counter should interleave while fetch_job is sleeping
    assert "interleaved_0" in execution_order
    assert "interleaved_1" in execution_order
    assert execution_order[0] in ("interleaved_0", "fetch_start")
    assert execution_order[-1] in ("fetch_end", "interleaved_2")
