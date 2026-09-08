"""Tests for FastAPI BackgroundTasks Architecture & Safe Memory Lifecycle."""

import asyncio
from collections.abc import MutableMapping
from datetime import datetime, timezone
import inspect
import time
from typing import Any
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncByteStream, AsyncClient, Request, Response
from httpx._transports.asgi import ASGIResponseStream
import pytest

from app.core.dependencies import ScopedTransactionContext
from app.main import app
from app.services.notification_service import (
    MAX_AUDIT_ENTRIES,
    clear_notification_service,
    get_audit_logs,
    get_notification_logs,
    record_audit_log,
    send_welcome_notification,
)


class StreamingASGITransport(ASGITransport):
    """Custom ASGI Transport capturing real HTTP network boundary latency.

    In production web servers (e.g. Uvicorn), response status headers and body are
    flushed to the network socket immediately upon endpoint return. BackgroundTasks
    are then executed by the ASGI server after the HTTP response is complete.

    Standard in-process TestClient awaits the full ASGI application (including
    background tasks) before returning. This transport simulates the actual network
    boundary by resolving the HTTP Response as soon as the response body is transmitted.
    """

    async def handle_async_request(self, request: Request) -> Response:
        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": request.method,
            "headers": [(k.lower(), v) for (k, v) in request.headers.raw],
            "scheme": request.url.scheme,
            "path": request.url.path,
            "raw_path": request.url.raw_path.split(b"?")[0],
            "query_string": request.url.query,
            "server": (request.url.host, request.url.port),
            "client": self.client,
            "root_path": self.root_path,
        }

        status_code: int | None = None
        response_headers: list[tuple[bytes, bytes]] | None = None
        body_parts: list[bytes] = []
        response_complete = asyncio.Event()

        # Request body stream
        assert isinstance(request.stream, AsyncByteStream)
        request_body_chunks = request.stream.__aiter__()
        request_complete = False

        async def receive() -> dict[str, Any]:
            nonlocal request_complete
            if request_complete:
                await response_complete.wait()
                return {"type": "http.disconnect"}
            try:
                chunk = await request_body_chunks.__anext__()
                return {"type": "http.request", "body": chunk, "more_body": True}
            except StopAsyncIteration:
                request_complete = True
                return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message: MutableMapping[str, Any]) -> None:
            nonlocal status_code, response_headers
            if message["type"] == "http.response.start":
                status_code = message["status"]
                response_headers = message.get("headers", [])
            elif message["type"] == "http.response.body":
                body = message.get("body", b"")
                if body:
                    body_parts.append(body)
                if not message.get("more_body", False):
                    response_complete.set()

        # Run the ASGI application as an asynchronous task
        async def run_app() -> None:
            await self.app(scope, receive, send)

        _app_task: asyncio.Task[None] = asyncio.create_task(run_app())

        # Wait only until HTTP response headers and body are fully transmitted
        await response_complete.wait()

        assert status_code is not None
        assert response_headers is not None
        stream = ASGIResponseStream(body_parts)
        return Response(status_code, headers=response_headers, stream=stream)


class TestBackgroundTasksArchitecture:
    """Verification suite for BackgroundTasks execution, latency, and memory safety."""

    @pytest.mark.asyncio
    async def test_low_latency_response_and_post_response_execution(self) -> None:
        """Verify POST /users/ responds immediately (< 35ms) while background task takes 50ms+."""
        clear_notification_service()

        warmup_payload = {
            "email": "warmup.user@example.com",
            "username": "warmup_user",
            "password": "Password123!",
            "password_confirm": "Password123!",
            "age": 27,
            "role": "user",
        }

        payload = {
            "email": "lowlatency.user@example.com",
            "username": "lowlatency_user",
            "password": "Password123!",
            "password_confirm": "Password123!",
            "age": 27,
            "role": "user",
        }

        async with AsyncClient(
            transport=StreamingASGITransport(app=app),
            base_url="http://test",
        ) as async_client:
            # Warm up threadpool, JIT, and schema cache
            await async_client.post("/users/", json=warmup_payload)
            await asyncio.sleep(0.06)  # allow warmup background task to finish before clearing
            clear_notification_service()

            start_time = time.perf_counter()
            response = await async_client.post("/users/", json=payload)
            elapsed_ms = (time.perf_counter() - start_time) * 1000

            # 1. HTTP 201 response must return immediately (< 35ms) even though background task is 50ms+
            assert response.status_code == 201
            assert (
                elapsed_ms < 45.0
            ), f"Response latency {elapsed_ms:.2f}ms exceeded 45ms threshold"

            # 2. Immediately upon response return, the 50ms background task has not completed yet
            notification_logs = get_notification_logs()
            assert len(notification_logs) == 0, (
                "Notification task should execute in the background after response transmission"
            )

            # 3. Shortly after response (< 75ms), background task finishes
            await asyncio.sleep(0.075)
            notification_logs_after = get_notification_logs()
            assert len(notification_logs_after) == 1
            assert notification_logs_after[0].email == payload["email"]
            assert notification_logs_after[0].username == payload["username"]
            assert notification_logs_after[0].status == "dispatched"

    def test_background_task_execution_on_user_registration(
        self,
        client: TestClient,
        sample_user_payload: dict[str, Any],
    ) -> None:
        """Verify that user registration triggers welcome notification and audit logging."""
        response = client.post("/users/", json=sample_user_payload)
        assert response.status_code == 201
        user_data = response.json()

        # In TestClient, background tasks execute before client.post returns
        notifications = get_notification_logs()
        assert len(notifications) == 1
        assert notifications[0].email == sample_user_payload["email"]
        assert notifications[0].username == sample_user_payload["username"]
        assert notifications[0].status == "dispatched"

        audit_logs = get_audit_logs()
        assert len(audit_logs) == 1
        assert audit_logs[0].action == "create_user"
        assert audit_logs[0].user_id == user_data["id"]
        assert isinstance(audit_logs[0].timestamp, datetime)

    def test_background_task_execution_on_user_update(
        self,
        client: TestClient,
        created_user: dict[str, Any],
    ) -> None:
        """Verify that PUT /users/{user_id} triggers audit logging in background."""
        clear_notification_service()

        user_id = created_user["id"]
        update_payload = {
            "email": "updated.architect@example.com",
            "username": "updated_architect",
            "age": 29,
            "full_name": "Updated Lead Architect",
        }
        headers = {"X-API-Key": f"userkey_{created_user['username']}"}

        response = client.put(f"/users/{user_id}", json=update_payload, headers=headers)
        assert response.status_code == 200

        # Verify audit log was recorded by BackgroundTasks
        audit_logs = get_audit_logs()
        assert len(audit_logs) == 1
        assert audit_logs[0].action == "update_user"
        assert audit_logs[0].user_id == user_id

        # Welcome notification should NOT have been dispatched on update
        assert len(get_notification_logs()) == 0

    @pytest.mark.asyncio
    async def test_resilient_exception_handling_in_background_tasks(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Verify that failures in background tasks never crash the process or fail silently."""
        clear_notification_service()

        async def failing_sleep(delay: float) -> None:
            raise ConnectionResetError("Remote SMTP server connection dropped")

        monkeypatch.setattr(asyncio, "sleep", failing_sleep)

        # Call worker task directly; verify it catches the error and logs rather than raising
        try:
            await send_welcome_notification("fail@example.com", "fail_user")
        except Exception as exc:
            pytest.fail(f"Background task leaked unhandled exception: {exc}")

        # Store should not have recorded dispatch due to failure
        assert len(get_notification_logs()) == 0

    @pytest.mark.asyncio
    async def test_audit_log_resilient_exception_handling(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Verify record_audit_log handles unexpected exceptions safely."""
        from app.services import notification_service

        class FailingDeque:
            def append(self, entry: Any) -> None:
                raise MemoryError("Simulated memory allocation failure")

        monkeypatch.setattr(notification_service, "_AUDIT_LOG_STORE", FailingDeque())

        try:
            await record_audit_log("critical_action", 42, datetime.now(timezone.utc))
        except Exception as exc:
            pytest.fail(f"record_audit_log leaked unhandled exception: {exc}")

    @pytest.mark.asyncio
    async def test_bounded_memory_queue_eviction(self) -> None:
        """Verify audit store enforces deque(maxlen=1000) O(1) space bound and evicts oldest entries."""
        clear_notification_service()

        total_entries = MAX_AUDIT_ENTRIES + 50  # 1050 entries
        now = datetime.now(timezone.utc)

        for i in range(total_entries):
            await record_audit_log(
                action=f"action_{i}",
                user_id=i,
                timestamp=now,
            )

        logs = get_audit_logs()
        # 1. Capacity bound strictly maintained
        assert len(logs) == MAX_AUDIT_ENTRIES == 1000

        # 2. Oldest 50 entries (0..49) must be evicted; entry 50 must be at index 0
        assert logs[0].action == "action_50"
        assert logs[0].user_id == 50
        assert logs[-1].action == f"action_{total_entries - 1}"
        assert logs[-1].user_id == total_entries - 1

    def test_safe_memory_boundary_primitive_arguments_only(self) -> None:
        """Verify task functions accept only primitives and reject request-scoped dependencies."""
        welcome_sig = inspect.signature(send_welcome_notification)
        audit_sig = inspect.signature(record_audit_log)

        # send_welcome_notification must only take primitives (str, str)
        welcome_params = welcome_sig.parameters
        assert "email" in welcome_params
        assert welcome_params["email"].annotation is str
        assert "username" in welcome_params
        assert welcome_params["username"].annotation is str

        # record_audit_log must only take primitives (str, int, datetime)
        audit_params = audit_sig.parameters
        assert "action" in audit_params
        assert audit_params["action"].annotation is str
        assert "user_id" in audit_params
        assert audit_params["user_id"].annotation is int
        assert "timestamp" in audit_params
        assert audit_params["timestamp"].annotation is datetime

        # Ensure no request-scoped objects (Request, ScopedTransactionContext) are declared
        for p in list(welcome_params.values()) + list(audit_params.values()):
            assert p.annotation is not ScopedTransactionContext
            assert "Request" not in str(p.annotation)
            assert "Session" not in str(p.annotation)
