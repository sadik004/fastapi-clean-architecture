# RCA: Day 44 - FastAPI Header Default Declaration Assertion & Async TestClient Event Loop Cross-Binding

- **Date**: 2026-09-09
- **Trigger**: FastAPI endpoint compilation failure and test execution runtime crashes during Day 44 implementation:
  1. `AssertionError: Header default value cannot be set in Annotated for 'authorization'. Set the default value with = instead.` during FastAPI application startup.
  2. `RuntimeError: <Queue ...> is bound to a different event loop` when running synchronous `TestClient` inside an `@pytest.mark.asyncio` test interacting with `fake_redis`.
  3. Bandit linter warnings `S105` and `S106` (hardcoded password assignment) on `jwt_secret_key` and `token_type="bearer"`.

- **Faulty Code / Pattern**:
  ```python
  # FLAW 1: Setting default inside Header() within Annotated
  async def get_current_authenticated_user(
      authorization: Annotated[
          str | None,
          Header(default=None, alias="Authorization"),  # <-- CRASHES FastAPI route compilation!
      ] = None,
  ) -> AuthenticatedUserResponse:
      ...

  # FLAW 2: Mixing synchronous TestClient with async fake_redis fixture in pytest.mark.asyncio
  @pytest.mark.asyncio
  async def test_refresh_token_rotation(client: TestClient, fake_redis: Any) -> None:
      # TestClient creates its own internal event loop thread, while fake_redis belongs
      # to pytest-asyncio's test loop, triggering cross-loop Queue RuntimeError!
      res = client.post("/auth/refresh", json=...)
  ```

- **Root Cause**:
  1. **FastAPI Dependency Signature Invariant**:
     In FastAPI with Python 3.9+ `typing.Annotated`, when a parameter specifies a default value via the function signature (e.g. `= None`), declaring `default=...` inside `Header(...)` or `Query(...)` creates an ambiguous dual-default declaration. FastAPI's `analyze_param` strictly asserts `field_info.default == Undefined or field_info.default == RequiredParam` and raises an immediate `AssertionError`.
  2. **Event Loop Cross-Binding in Async Tests**:
     `fakeredis.aioredis.FakeRedis` uses internal `asyncio.Queue` primitives bound to the event loop active when `fake_redis` is instantiated. When a test marked with `@pytest.mark.asyncio` uses `TestClient(app)`, `TestClient` spawns a separate worker thread with its own private event loop. When the FastAPI application inside `TestClient` calls `await redis.delete()`, it attempts to retrieve responses from the queue belonging to pytest's event loop, triggering `RuntimeError: <Queue> is bound to a different event loop`.
  3. **Bandit Static Security Heuristic (S105 / S106)**:
     Flake8-bandit scans for identifiers containing `secret`, `password`, or `token` assigned to string literals. A default setting `jwt_secret_key = "..."` and parameter `token_type = "bearer"` trigger false-positive warnings.

- **Resolution**:
  1. **Removed Default from Header Metadata (`app/core/dependencies.py`)**:
     ```python
     async def get_current_authenticated_user(
         authorization: Annotated[
             str | None,
             Header(
                 alias="Authorization",
                 description="Stateless Bearer JWT token header",
             ),
         ] = None,  # Default declared exclusively on function signature
     ) -> AuthenticatedUserResponse:
     ```
  2. **Unified Event Loop via `httpx.AsyncClient` (`tests/test_jwt_and_refresh_token_rotation.py`)**:
     Used `httpx.AsyncClient` with `ASGITransport(app=app)` across all async tests. Both the test runner, the HTTP client, and `fake_redis` execute cooperatively on the exact same asyncio event loop:
     ```python
     transport = ASGITransport(app=app)
     async with AsyncClient(transport=transport, base_url="http://test") as ac:
         resp = await ac.post("/auth/login", json=...)
     ```
  3. **Explicit Bandit Exemption & Default Omission**:
     Added `# noqa: S105` to `jwt_secret_key` in `Settings` and omitted the redundant `token_type="bearer"` keyword argument during `TokenResponse` instantiation.

- **Permanent Prevention Rules**:
  - *Rule 81*: When using `Annotated[Type, Header(...)] = default`, never declare `default=...` inside `Header()` or `Query()`.
  - *RCA Day 32 / Day 44 Rule*: In `@pytest.mark.asyncio` tests interacting with asynchronous Redis (`fake_redis`), always use `httpx.AsyncClient(transport=ASGITransport(app=app))` rather than synchronous `TestClient`.
