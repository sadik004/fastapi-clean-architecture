# RCA: Day 48 - UUIDv7/ULID Intra-Millisecond Monotonicity & Offset-Naive Datetime Subtraction Alignment

- **Date**: 2026-09-09
- **Trigger**: During Day 48 implementation and testing of Time-Ordered Cryptographic Identifiers (UUIDv7 & ULID):
  1. Non-monotonic sorting failures in ULID generation tests when generating multiple IDs inside the exact same millisecond.
  2. `TypeError: can't subtract offset-naive and offset-aware datetimes` in `tests/test_time_ordered_uuidv7.py` when comparing extracted UUIDv7 UTC datetime with SQLite ORM `OrderModel.created_at`.
  3. `AssertionError` in `tests/test_alembic_migrations.py::test_migration_bidirectional_reversibility` when rolling back with `revision="-1"`.
  4. AST compliance failure in `tests/test_codebase_compliance.py` due to missing return type annotations on SQLAlchemy `TypeDecorator` methods.

- **Faulty Code / Pattern**:
  ```python
  # FLAW 1: Pure random entropy in ULID within the same millisecond clock tick
  def generate_ulid() -> str:
      timestamp_ms = int(time.time() * 1000)
      entropy = secrets.randbits(80)  # Pure random!
      # If two calls occur at timestamp T, entropy2 might be < entropy1,
      # causing ulid2 < ulid1 despite sequential invocation!
      return encode_crockford_base32(timestamp_ms, entropy)

  # FLAW 2: Comparing offset-aware extracted timestamp with offset-naive SQLite datetime
  extracted_ts = extract_timestamp_from_uuidv7(order.id)  # Returns tzinfo=timezone.utc
  # In SQLite, SQLAlchemy DateTime(timezone=True) often returns offset-naive datetime:
  diff = abs((extracted_ts - order.created_at).total_seconds())
  # Crashes with TypeError: can't subtract offset-naive and offset-aware datetimes!

  # FLAW 3: Migration rollback test assuming permissions was head - 1
  # When orders migration (432a46573144) became head, -1 rolled back orders, not permissions:
  rollback_migration(revision="-1")
  # Test assumed 'permissions' column was dropped, but 'orders' table was dropped!

  # FLAW 4: Missing return type annotations on TypeDecorator methods
  class GUID(TypeDecorator[uuid.UUID]):
      def load_dialect_impl(self, dialect):      # Missing '-> TypeEngine[Any]:'
          ...
      def process_bind_param(self, value, dialect): # Missing '-> Any:'
          ...
      def process_result_value(self, value, dialect): # Missing '-> uuid.UUID | None:'
          ...
  ```

- **Root Cause**:
  1. **ULID Sub-Millisecond Clock Monotonicity**:
     Standard ULID generation specs dictate that if multiple identifiers are generated within the identical millisecond timestamp, the randomness component must increment monotonically rather than generating independent random numbers. Independent random numbers create random order inversions within bursts.
  2. **Python Datetime Timezone Awareness Boundaries**:
     `extract_timestamp_from_uuidv7` explicitly returns a UTC-aware datetime (`datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc)`). However, SQLite does not have native timezone storage; SQLAlchemy maps `DateTime(timezone=True)` to naive UTC strings. Accessing `order.created_at` produces an offset-naive datetime. In Python, subtracting an offset-naive datetime from an offset-aware datetime raises an immediate `TypeError`.
  3. **Alembic Head Step-Down Graph Traversal**:
     Relative revision targets (`-1`) are resolved against the current `head`. Each newly added migration revision becomes the new `head`. Rollback tests must reflect the actual latest migration graph node.
  4. **Strict AST Return Type Compliance**:
     `tests/test_codebase_compliance.py` uses Python's `ast` module to verify that every method and function definition across `app/` contains an explicit return type annotation, enforcing compile-time type safety.

- **Resolution**:
  ```python
  # 1. Thread-Safe Intra-Millisecond Sequence Incrementing (app/core/identifiers.py)
  _last_ulid_timestamp_ms: int = -1
  _last_ulid_entropy: int = 0
  _ulid_lock = threading.Lock()

  def generate_ulid() -> str:
      global _last_ulid_timestamp_ms, _last_ulid_entropy
      with _ulid_lock:
          now_ms = int(time.time() * 1000)
          if now_ms > _last_ulid_timestamp_ms:
              _last_ulid_timestamp_ms = now_ms
              _last_ulid_entropy = secrets.randbits(80)
          else:
              # Monotonically increment entropy within identical millisecond
              _last_ulid_entropy = (_last_ulid_entropy + 1) & ((1 << 80) - 1)
              now_ms = _last_ulid_timestamp_ms
          return _encode_ulid(now_ms, _last_ulid_entropy)

  # 2. Timezone Normalization in Tests (tests/test_time_ordered_uuidv7.py)
  order_created_at = (
      order.created_at.replace(tzinfo=timezone.utc)
      if order.created_at.tzinfo is None
      else order.created_at
  )
  delta = abs((extracted_dt - order_created_at).total_seconds())
  assert delta < 10.0

  # 3. Sequential Migration Rollback Verification (tests/test_alembic_migrations.py)
  # -1 correctly rolls back orders table
  rollback_migration(revision="-1")
  inspector = inspect(db_engine.sync_engine)
  assert "orders" not in inspector.get_table_names()
  assert "products" in inspector.get_table_names()

  # 4. Explicit AST-Compliant Return Annotations (app/models/order.py)
  class GUID(TypeDecorator[uuid.UUID]):
      impl = CHAR
      cache_ok = True

      def load_dialect_impl(self, dialect: Dialect) -> TypeEngine[Any]:
          if dialect.name == "postgresql":
              return dialect.type_descriptor(UUID(as_uuid=True))
          return dialect.type_descriptor(CHAR(32))

      def process_bind_param(self, value: uuid.UUID | str | None, dialect: Dialect) -> Any:
          if value is None:
              return None
          if dialect.name == "postgresql":
              return value
          return str(value).replace("-", "")

      def process_result_value(self, value: Any, dialect: Dialect) -> uuid.UUID | None:
          if value is None:
              return None
          if isinstance(value, uuid.UUID):
              return value
          return uuid.UUID(str(value))
  ```

- **Permanent Prevention Rules**:
  - *Rule 90*: When implementing time-ordered identifiers (ULID/UUIDv7), guarantee intra-millisecond monotonicity by incrementing a sequence/entropy counter within a thread lock when clock timestamps collide.
  - *Rule 91*: When comparing timestamps between storage and domain derivations, always normalize datetimes to explicit UTC awareness (`dt.replace(tzinfo=timezone.utc)` if `dt.tzinfo is None`).
  - *Rule 92*: All SQLAlchemy `TypeDecorator` methods must include explicit return type annotations (`-> TypeEngine[Any]`, `-> Any`) to satisfy strict AST static analysis invariants.
