# RCA: Day 40 - Pessimistic Locking Dual-Layer Concurrency Defense & Test Fixture Lock Registry Isolation

- **Date**: 2026-09-09
- **Trigger**: Concurrency race hazard during high-contention flash sale test engineering (15 concurrent checkout requests competing for 5 units of stock):
  1. In SQLite / `aiosqlite` test environments, `SELECT ... FOR UPDATE` does not provide true multi-connection row-level locking (it is either a no-op or causes database-wide file table lock collisions `OperationalError: database is locked`).
  2. In an asynchronous event loop, concurrent coroutines executing `await session.execute()` and `await session.flush()` can interleave execution between the stock check (`if model.stock < quantity`) and the deduction write, creating an in-memory Time-of-Check to Time-of-Use (TOCTOU) race condition during testing.
  3. Initial lock registry (`_product_row_locks`) lacked teardown hooks, creating a state pollution risk across pytest test cases.

- **Faulty Code / Pattern**:
  ```python
  # FLAW 1: Relying solely on SQL with_for_update() in SQLite async test environments
  async def deduct_stock_pessimistic(self, product_id: int, quantity: int) -> ProductEntity:
      stmt = select(ProductModel).where(ProductModel.id == product_id).with_for_update()
      result = await self._session.execute(stmt)
      model = result.scalar_one_or_none()
      # Under SQLite aiosqlite, concurrent coroutines interleave here!
      if model.stock < quantity:
          raise InsufficientStockException()
      model.stock -= quantity
      await self._session.flush()
      return self._to_entity(model)
  ```
  And omitting lock cleanup in test teardown:
  ```python
  # FLAW 2: tests/conftest.py cleaned tables but omitted in-memory row lock registry reset
  def clean_repo() -> Generator[UserRepositoryProtocol]:
      _user_repository.clear()
      _clean_database()  # Locks in _product_row_locks remain active/dirty across tests!
  ```

- **Root Cause**:
  1. **SQLite Concurrency & Row-Lock Limitations**: PostgreSQL and MySQL implement genuine row-level exclusive locks (e.g. InnoDB row locks or Postgres tuple locks) via `SELECT ... FOR UPDATE`. However, SQLite is an embedded file database without granular row-level locking. Under async test runtimes with `aiosqlite`, multiple coroutines sharing connection pools can observe the same uncommitted stock level if execution yields at an `await` boundary before the flush/commit completes.
  2. **Inter-Test State Pollution**: When using an application-level row-lock registry (`_product_row_locks: dict[int, asyncio.Lock]`) to provide coroutine serialization in async runtimes, lock instances persist in process memory. If a test fails mid-execution or aborts while holding a lock, subsequent tests attempting to checkout the same product ID would deadlock waiting for the dangling lock.

- **Resolution**:
  1. **Dual-Layer Concurrency Defense**:
     - At the SQL layer: Kept `select(...).with_for_update()` for production PostgreSQL/MySQL row-level locking within the active Unit of Work transaction boundary.
     - At the application/coroutine layer: Implemented an $\mathcal{O}(1)$ row-level mutex registry (`get_product_lock(product_id)`) using `asyncio.Lock`. This guarantees cooperative serialization across competing coroutines within the Python event loop:
       ```python
       async def deduct_stock_pessimistic(self, product_id: int, quantity: int) -> ProductEntity:
           row_lock = get_product_lock(product_id)
           async with row_lock:
               stmt = select(ProductModel).where(ProductModel.id == product_id).with_for_update()
               result = await self._session.execute(stmt)
               model = result.scalar_one_or_none()
               if model is None:
                   raise ProductNotFoundException(product_id=product_id)
               if model.stock < quantity:
                   raise InsufficientStockException()
               model.stock -= quantity
               await self._session.flush()
               return self._to_entity(model)
       ```
  2. **Automated Lock Registry Teardown**:
     - Exported `clear_product_locks()` in `app/repositories/product_repository.py`.
     - Integrated `clear_product_locks()` into both the setup and teardown phases of the `clean_repo` autouse fixture in `tests/conftest.py`.

- **Permanent Prevention Rules**:
  1. **Dual-Layer Concurrency in Async Systems**: When building pessimistic locking systems that must run deterministically in both production (Postgres `with_for_update`) and local/CI test environments (SQLite `aiosqlite`), always complement SQL row locks with coroutine-level row mutexes (`asyncio.Lock` keyed by entity ID).
  2. **Clean All In-Memory Concurrency State in Fixtures**: Any mutable global or module-level concurrency primitive (`dict[int, asyncio.Lock]`, semaphores, queues) MUST have an explicit reset function wired into the autouse test cleanup fixture (`conftest.py`).
  3. **Zero External I/O in Pessimistic Critical Sections**: Never hold a row-level lock (SQL or asyncio) while making external HTTP requests, background task dispatches, or heavy computations. The critical section must strictly encompass: acquire lock $\to$ validate stock $\to$ decrement $\to$ flush/commit.
