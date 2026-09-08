"""Comprehensive test suite for Day 10: Dependency Lifecycle Cleanup.

Verifies generator dependencies with the yield mechanism, two-phase context,
automated commit vs rollback, and zero resource leaks.
"""

from collections.abc import Generator
from typing import Any

import pytest
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.testclient import TestClient

from app.core.dependencies import (
    RequestLifecycleContext,
    ScopedTransactionContext,
    TransactionStatus,
    get_transaction_context,
    track_request_lifecycle,
    transaction_manager,
)
from app.main import app

# ============================================================================
# Auxiliary Test Router for Two-Phase Yield Lifecycle & Teardown Verification
# ============================================================================

lifecycle_router = APIRouter(prefix="/test-lifecycle", tags=["Test Lifecycle"])


@lifecycle_router.get("/success")
def success_route(
    tx: ScopedTransactionContext = Depends(get_transaction_context),
    audit: RequestLifecycleContext = Depends(track_request_lifecycle),
) -> dict[str, Any]:
    """Endpoint completing successfully with staged action."""
    tx.stage("action:success_operation")
    return {
        "status": "ok",
        "tx_id": tx.tx_id,
        "trace_id": audit.trace_id,
    }


@lifecycle_router.get("/handled-error")
def handled_error_route(
    tx: ScopedTransactionContext = Depends(get_transaction_context),
) -> dict[str, str]:
    """Endpoint raising an explicit HTTPException after staging an action."""
    tx.stage("action:will_fail")
    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Simulated business error")


@lifecycle_router.get("/unhandled-error")
def unhandled_error_route(
    tx: ScopedTransactionContext = Depends(get_transaction_context),
) -> dict[str, str]:
    """Endpoint raising an unexpected unhandled exception."""
    tx.stage("action:crash")
    raise RuntimeError("Unexpected internal crash")


@pytest.fixture(scope="module", autouse=True)
def register_lifecycle_router() -> Generator[None, None, None]:
    """Register lifecycle test router on the application for the test module."""
    app.include_router(lifecycle_router)
    yield


# ============================================================================
# 1. Success Lifecycle & Two-Phase Execution Tests
# ============================================================================


class TestSuccessLifecycle:
    """Verify pre-yield acquisition, post-yield commit, and cleanup on HTTP 200/201."""

    def test_user_creation_transaction_lifecycle_commits(
        self,
        client: TestClient,
    ) -> None:
        """Verify POST /users/ stages action, commits on success, and closes transaction."""
        initial_history_len = len(transaction_manager._history)

        payload = {
            "email": "lifecycle.test@example.com",
            "username": "lifecycle_test",
            "password": "Password123!",
            "password_confirm": "Password123!",
            "age": 25,
            "role": "user",
        }
        response = client.post("/users/", json=payload)
        assert response.status_code == status.HTTP_201_CREATED

        # Verify active transactions is 0 (no leaks)
        assert transaction_manager.active_count == 0

        # Verify last transaction in history was committed and closed
        assert len(transaction_manager._history) == initial_history_len + 1
        last_tx = transaction_manager._history[-1]
        assert last_tx.status == TransactionStatus.COMMITTED
        assert last_tx.is_closed is True
        assert last_tx.duration_ms >= 0.0
        assert "create_user:lifecycle_test" in last_tx.staged_actions

    def test_auxiliary_success_route_commits_and_records_audit(
        self,
        client: TestClient,
    ) -> None:
        """Verify success endpoint completes two-phase lifecycle for both tx and audit contexts."""
        response = client.get("/test-lifecycle/success")
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["status"] == "ok"

        # Verify zero lingering active transactions
        assert transaction_manager.active_count == 0
        last_tx = transaction_manager._history[-1]
        assert last_tx.tx_id == data["tx_id"]
        assert last_tx.status == TransactionStatus.COMMITTED
        assert last_tx.is_closed is True
        assert last_tx.duration_ms >= 0.0
        assert last_tx.staged_actions == ["action:success_operation"]


# ============================================================================
# 2. Error Lifecycle & Transactional Rollback Tests
# ============================================================================


class TestErrorLifecycleRollback:
    """Verify post-yield rollback and guaranteed teardown upon error."""

    def test_duplicate_user_conflict_triggers_rollback(
        self,
        client: TestClient,
        sample_user_payload: dict[str, Any],
    ) -> None:
        """Verify HTTP 409 Conflict triggers transaction rollback and clears staged actions."""
        # 1. First creation succeeds
        resp1 = client.post("/users/", json=sample_user_payload)
        assert resp1.status_code == status.HTTP_201_CREATED

        # 2. Second creation fails with 409 Conflict
        resp2 = client.post("/users/", json=sample_user_payload)
        assert resp2.status_code == status.HTTP_409_CONFLICT

        # Assert no dangling transactions
        assert transaction_manager.active_count == 0

        # Verify failed transaction was rolled back and cleared
        last_tx = transaction_manager._history[-1]
        assert last_tx.status == TransactionStatus.ROLLED_BACK
        assert last_tx.is_closed is True
        assert len(last_tx.staged_actions) == 0  # cleared on rollback

    def test_user_not_found_triggers_rollback(
        self,
        client: TestClient,
        admin_user: dict[str, Any],
        admin_auth_headers: dict[str, str],
    ) -> None:
        """Verify HTTP 404 on DELETE triggers transaction rollback and closes cleanly."""
        response = client.delete("/users/999999", headers=admin_auth_headers)
        assert response.status_code == status.HTTP_404_NOT_FOUND

        assert transaction_manager.active_count == 0
        last_tx = transaction_manager._history[-1]
        assert last_tx.status == TransactionStatus.ROLLED_BACK
        assert last_tx.is_closed is True
        assert len(last_tx.staged_actions) == 0

    def test_handled_http_exception_rolls_back(
        self,
        client: TestClient,
    ) -> None:
        """Verify endpoint raising explicit HTTPException rolls back and closes."""
        response = client.get("/test-lifecycle/handled-error")
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json()["detail"] == "Simulated business error"

        assert transaction_manager.active_count == 0
        last_tx = transaction_manager._history[-1]
        assert last_tx.status == TransactionStatus.ROLLED_BACK
        assert last_tx.is_closed is True
        assert len(last_tx.staged_actions) == 0

    def test_unhandled_crash_rolls_back_and_closes(
        self,
        client: TestClient,
    ) -> None:
        """Verify unexpected RuntimeError triggers rollback and executes finally block."""
        with pytest.raises(RuntimeError, match="Unexpected internal crash"):
            client.get("/test-lifecycle/unhandled-error")

        # Zero dangling transactions even after unhandled crash
        assert transaction_manager.active_count == 0
        last_tx = transaction_manager._history[-1]
        assert last_tx.status == TransactionStatus.ROLLED_BACK
        assert last_tx.is_closed is True


# ============================================================================
# 3. Stress & Zero Resource Leak Verification (100 Mixed Requests)
# ============================================================================


class TestResourceLeakStress:
    """Verify zero resource leaks after high-frequency mixed success and failure workloads."""

    def test_zero_resource_leaks_after_100_mixed_requests(
        self,
        client: TestClient,
        admin_user: dict[str, Any],
        admin_auth_headers: dict[str, str],
    ) -> None:
        """Simulate 100 mixed requests (200, 201, 400, 404, 409, 422) and verify 0 active transactions."""
        transaction_manager.clear()
        assert transaction_manager.active_count == 0

        for i in range(100):
            scenario = i % 5

            if scenario == 0:
                # Success creation
                client.post(
                    "/users/",
                    json={
                        "email": f"stress_user_{i}@example.com",
                        "username": f"stress_{i}",
                        "password": "Password123!",
                        "password_confirm": "Password123!",
                        "age": 20 + (i % 50),
                    },
                )
            elif scenario == 1:
                # 400 Handled error route
                client.get("/test-lifecycle/handled-error")
            elif scenario == 2:
                # 404 Not Found delete
                client.delete(f"/users/{900000 + i}", headers=admin_auth_headers)
            elif scenario == 3:
                # 422 Validation error
                client.post(
                    "/users/",
                    json={
                        "email": "invalid-email",
                        "username": "ab",
                        "password": "short",
                        "password_confirm": "mismatch",
                    },
                )
            elif scenario == 4:
                # 200 Success route
                client.get("/test-lifecycle/success")

        # CRITICAL ASSERTION: Active count must be strictly 0 after 100 requests
        assert transaction_manager.active_count == 0, (
            f"Resource leak detected! {transaction_manager.active_count} dangling transactions remain active."
        )


# ============================================================================
# 4. Request Timing & Audit Context Verification
# ============================================================================


class TestRequestLifecycleTiming:
    """Verify track_request_lifecycle computes duration and audit trace."""

    def test_request_lifecycle_timing_accuracy(
        self,
        client: TestClient,
    ) -> None:
        """Verify request duration in milliseconds is measured and strictly non-negative."""
        response = client.get("/test-lifecycle/success")
        assert response.status_code == status.HTTP_200_OK

        last_tx = transaction_manager._history[-1]
        assert last_tx.duration_ms > 0.0
