# RCA: Day 15 SQLAlchemy 2.0 Async Setup, Expire-On-Commit Invariant & Pytest-Asyncio Fixtures

- **Trigger**: Initial test failure (`sqlite3.OperationalError: no such table: day15_sample_test_entities`) due to sync pytest fixture decorator on an async fixture in strict mode, and proving the critical `expire_on_commit=False` greenlet lifecycle invariant.

---

## 1. Incident 1: Async Fixture Ignored in Pytest-Asyncio Strict Mode

### Faulty Code / Pattern
```python
# tests/test_database_async_setup.py
import pytest

@pytest.fixture(autouse=True)  # Standard synchronous pytest fixture decorator!
async def setup_test_tables() -> AsyncGenerator[None, None]:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
```

### Root Cause
In `pytest-asyncio` strict mode (`asyncio_mode = strict`), decorating an asynchronous generator fixture (`async def`) with standard `@pytest.fixture` causes pytest to fail to await or enter the async generator context. As a result:
1. Pytest issues a deprecation warning: `PytestDeprecationWarning: asyncio test requested async @pytest.fixture in strict mode. You might want to use @pytest_asyncio.fixture`.
2. The fixture body never executes table creation before the test.
3. Tests attempting table insertions fail immediately with `sqlite3.OperationalError: no such table`.

### Resolution
Always import and use `@pytest_asyncio.fixture` explicitly for asynchronous test setup and teardown fixtures:
```python
import pytest_asyncio

@pytest_asyncio.fixture(autouse=True)
async def setup_test_tables() -> AsyncGenerator[None, None]:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
```

---

## 2. Incident 2: MissingGreenlet Exception on Post-Commit Attribute Access (`expire_on_commit=True`)

### Faulty Code / Pattern
```python
# app/core/database.py
async_session_factory = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=True,  # Default SQLAlchemy behavior!
)

# Endpoint or test:
async with async_session_factory() as session:
    entity = User(username="architect")
    session.add(entity)
    await session.commit()
    # CRASH: Accessing entity.username triggers implicit synchronous lazy refresh!
    print(entity.username)
    # Raises: sqlalchemy.exc.MissingGreenlet: greenlet_spawn has not been spawned; can't call await_only() here
```

### Root Cause
In classical synchronous SQLAlchemy ORM, when a transaction is committed, `expire_on_commit=True` expires all persisted instances. Accessing attributes on expired instances triggers a synchronous database query to refresh state.
In SQLAlchemy 2.0 asynchronous I/O (`AsyncSession`), synchronous database I/O is impossible on the main event loop thread without a greenlet context. Calling `entity.attribute` on an expired instance attempts an `await_only()` call outside greenlet spawn, raising `sqlalchemy.exc.MissingGreenlet`.

### Resolution
Unconditionally configure `expire_on_commit=False` on the `async_sessionmaker`:
```python
# app/core/database.py
async_session_factory: async_sessionmaker[AsyncSession] = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,  # Mandatory invariant for modern AsyncSession!
    autoflush=False,
)
```
When `expire_on_commit=False`, model attributes remain safely loaded in Python memory post-commit, eliminating implicit lazy queries and runtime greenlet crashes.

---

## 3. Incident 3: Dangling Connection Pool Sockets on Application Teardown

### Faulty Code / Pattern
```python
# app/main.py without lifespan shutdown hook
app = FastAPI()

# When the application process receives SIGTERM or exits,
# database pool sockets are left dangling, leading to socket exhaustion
# or leaked backend connections on server reload/restarts.
```

### Root Cause
SQLAlchemy engines manage an internal connection pool holding open TCP/domain sockets. If the application terminates without calling `await engine.dispose()`, connections remain open on the database server until connection timeout or OS garbage collection, causing connection pool exhaustion during rolling container deployments.

### Resolution
Implement FastAPI's `@asynccontextmanager async def lifespan(app: FastAPI)` and explicitly dispose the engine during application shutdown:
```python
@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    # Startup: Initialize tables
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    # Shutdown: Cleanly terminate all connection pool sockets
    await engine.dispose()

app = FastAPI(..., lifespan=lifespan)
```

---

## Permanent Prevention Rules Added to SKILL.md
1. **Always use `@pytest_asyncio.fixture`**: In pytest-asyncio strict mode, never use `@pytest.fixture` on `async def` fixtures.
2. **Mandatory `expire_on_commit=False`**: Every `async_sessionmaker` MUST explicitly declare `expire_on_commit=False` to prevent `MissingGreenlet` crashes.
3. **Lifespan Socket Disposal**: Always attach `await engine.dispose()` to the FastAPI `lifespan` shutdown phase.
4. **Session Generator Dependency**: Always wrap `yield session` in `try...except...finally` to commit on clean exit, rollback on exception, and close in `finally:`.
