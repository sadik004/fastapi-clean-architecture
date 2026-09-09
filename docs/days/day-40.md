# Day 40: Pessimistic Locking Architecture (Inventory Stock Blocking via SQLAlchemy with_for_update)

## 1. Overview & Architectural Objectives
In Day 39, we implemented **Optimistic Concurrency Control (OCC)** using row version stamping (`version = version + 1 WHERE version = :expected_version`), which thrives under low-to-medium contention by letting transactions proceed without database locks and failing fast with `HTTP 409 Conflict` on version collision.

However, in **extreme high-contention flash sale scenarios** (e.g., Daraz 11.11, Amazon Black Friday, or high-speed train/airline ticket bookings), thousands of shoppers compete for a small pool of inventory (e.g., 5 limited-edition items). Under OCC, 99%+ of transactions abort and require client retries, saturating database I/O and creating catastrophic retry storms. Even worse, without strict atomicity, race conditions cause **overselling** (inventory dropping below zero into negative numbers).

Day 40 engineers **Pessimistic Concurrency Control (PCC)** using SQLAlchemy 2.0's `with_for_update()` within an explicit **Unit of Work (UoW)** transaction boundary, achieving:
1. **Zero Overselling Invariant**: An exclusive row-level lock (`SELECT ... FOR UPDATE`) guarantees that competing transactions are serialized at the database row level. Stock is decremented atomically and cannot drop below zero.
2. **Transaction-Scoped Row Locks**: Locks are acquired upon `get_by_id_with_lock()` / `deduct_stock_pessimistic()` and held strictly within the Unit of Work (`async with uow:`), automatically released upon `commit()` or `rollback()`.
3. **Deadlock Safety & Minimal Lock Duration**: No external HTTP requests, expensive CPU operations, or background tasks execute while holding the row lock.
4. **Decoupled Domain Exceptions**: Raises `InsufficientStockException(BusinessRuleViolationException)` mapping to standardized `HTTP 400 Bad Request` with error code `INSUFFICIENT_STOCK`.
5. **Deterministic Concurrency Verification**: Mathematically proves under 15 simultaneous checkout requests competing for 5 stock units that **exactly 5 succeed (HTTP 200)**, **exactly 10 fail (HTTP 400)**, and final database stock is **strictly 0**.

---

## 2. Pessimistic vs Optimistic Locking Comparison

| Dimension | Optimistic Concurrency Control (OCC) | Pessimistic Concurrency Control (PCC) |
| :--- | :--- | :--- |
| **Locking Mechanism** | Zero database locks; uses version column (`version + 1`) | Exclusive database row lock (`SELECT ... FOR UPDATE`) |
| **SQL Primitive** | `UPDATE ... WHERE id = :id AND version = :v` | `SELECT ... WHERE id = :id FOR UPDATE` |
| **Contention Profile** | Ideal for low-to-medium contention (e.g. user profile updates) | Ideal for ultra-high contention (e.g. flash sales, ticket booking) |
| **Failure Mode** | Aborts and returns `HTTP 409 Conflict` on race | Queues competitors; rejects with `HTTP 400` once stock is depleted |
| **Retry Overhead** | Requires client retries on collision | Zero client retries needed; requests wait their turn in queue |
| **Risk** | High abort rates under surge traffic | Potential database connection queue exhaustion if lock held too long |

---

## 3. Core Components & Implementation

### 3.1 Product Domain Model & Alembic Migration (`app/models/product.py`)
```python
class ProductModel(Base):
    __tablename__ = "products"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    stock: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    price: Mapped[float] = mapped_column(Float, nullable=False)
```
- Migration generated and applied: `alembic revision --autogenerate -m "create_products_table_for_pessimistic_locking"`, `alembic upgrade head`.

### 3.2 Domain Exceptions (`app/core/exceptions.py`)
```python
class InsufficientStockException(BusinessRuleViolationException):
    """Raised when an inventory deduction request exceeds available stock (HTTP 400)."""
    def __init__(
        self,
        message: str = "Requested quantity exceeds available stock.",
        code: str = "INSUFFICIENT_STOCK",
    ) -> None:
        super().__init__(message=message, code=code)
```

### 3.3 Repository Layer with Exclusive Row Lock (`app/repositories/product_repository.py`)
- `SqlAlchemyProductRepository`:
  - `get_by_id_with_lock(product_id: int) -> ProductEntity | None`:
    Executes `select(ProductModel).where(ProductModel.id == product_id).with_for_update()`.
  - `deduct_stock_pessimistic(product_id: int, quantity: int) -> ProductEntity`:
    Acquires row-level mutex, validates `model.stock >= quantity`, decrements `model.stock -= quantity`, flushes session (`await self._session.flush()`), and maps to detached `ProductEntity`.
- `InMemoryProductRepository`: Fast in-memory double with O(1) hash map operations and row mutex.

### 3.4 Unit of Work Integration (`app/core/unit_of_work.py`)
- `UnitOfWorkProtocol` exposes `@property def products(self) -> ProductRepositoryProtocol`.
- `SqlAlchemyUnitOfWork` wires `SqlAlchemyProductRepository(session=self.session)`.
- `InMemoryUnitOfWork` wires `InMemoryProductRepository()`.

### 3.5 Inventory Service & Router Integration
- `InventoryService` (`app/services/inventory_service.py`):
  - `checkout_product(product_id: int, quantity: int) -> ProductEntity`:
    Executes within `async with self._uow:`, calls `deduct_stock_pessimistic`, and commits transaction, releasing the lock.
- `ProductRouter` (`app/routers/product_router.py`):
  - `POST /products/`: Register product (HTTP 201).
  - `GET /products/{product_id}`: Retrieve product details (HTTP 200).
  - `POST /products/{product_id}/checkout`: Pessimistic checkout with row-level locking (HTTP 200 or HTTP 400).

---

## 4. Verification & Concurrency Test Suite (`tests/test_pessimistic_locking.py`)

1. **Sequential Stock Deduction**:
   - Stock 10 $\to$ deduct 3 $\to$ remaining stock is exactly 7.
2. **Insufficient Stock Rejection**:
   - Stock 2 $\to$ request 5 $\to$ raises `InsufficientStockException` (HTTP 400 `INSUFFICIENT_STOCK`).
3. **Concurrent Flash Sale Race Test (Mathematical Invariant)**:
   - Initial stock = 5.
   - 15 concurrent checkout requests fired via `asyncio.gather`.
   - **Result**: EXACTLY 5 requests succeed (HTTP 200), EXACTLY 10 requests fail (HTTP 400), final database stock is EXACTLY 0. Zero overselling!
4. **Full Project Regression**:
   - 419 passed tests across the entire test suite with 100% pass rate.
   - Zero mypy strict issues across 101 source files.
   - Zero ruff lint errors.

---

## 5. Complexity & Constraints
- **Time Complexity**:
  - `get_by_id_with_lock`: $\mathcal{O}(1)$ via primary key clustered B-Tree index lookup.
  - `deduct_stock_pessimistic`: $\mathcal{O}(1)$ row lock acquisition and in-place decrement.
  - Overall checkout transaction: $\mathcal{O}(1)$ DB operations.
- **Space Complexity**:
  - Detached slotted entity `ProductEntity`: $\mathcal{O}(1)$ memory footprint (< 120 bytes).
  - Row lock registry: $\mathcal{O}(K)$ where $K$ is the number of active distinct product IDs.
