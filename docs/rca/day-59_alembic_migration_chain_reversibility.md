# RCA: Alembic Migration Chain Reversibility & Transactional Outbox Dual-Write Elimination

- **Milestone**: Day 59
- **Topic**: Transactional Outbox Pattern Architecture (Defeating the Dual-Write Problem for Zero Data Loss)
- **Trigger**: Test failure in `tests/test_alembic_migrations.py::test_migration_bidirectional_reversibility` after adding migration `2838c57dcc77_create_outbox_events_table.py`

---

## Faulty Code / Anti-Pattern

```python
# Anti-pattern 1: Dual-Write Anti-Pattern in Application Code
async def create_order(self, order_data: OrderCreate) -> Order:
    order = await self.db.orders.add(order_data)
    await self.db.commit()  # Step 1: Database commits successfully
    # Step 2: Network call to Kafka
    await self.kafka_producer.send_and_wait("orders.events", order.to_bytes())
    # If Kafka leader crashes or network partitions here, the event is permanently lost!
    # The database has committed, but downstream consumers will never know the order exists.
    return order

# Anti-pattern 2: Hardcoded Alembic Rollback Step Assumptions in Tests
def test_migration_bidirectional_reversibility(alembic_engine):
    # Test assumed head was 6c6aea5c9814 (nid_number column)
    command.downgrade(cfg, "-1")
    # Asserted that nid_number was dropped, but actually the new head was outbox_events!
    inspector = inspect(engine)
    columns = [col["name"] for col in inspector.get_columns("users")]
    assert "nid_number" not in columns  # FAILED: nid_number was still present!
```

---

## Root Cause

1. **Dual-Write Problem**:
   Performing two network writes (PostgreSQL commit and Kafka produce) across independent systems without a 2-Phase Commit (2PC) guarantees data inconsistency. When the first succeeds and the second fails, data is lost; when the first succeeds and the caller crashes before the second, downstream services are out of sync.

2. **Alembic Reversibility Test Chain Invariant**:
   In `tests/test_alembic_migrations.py`, the test `test_migration_bidirectional_reversibility` steps down relative revision `"-1"` from `head`. When a new migration `2838c57dcc77` was created for `outbox_events`, `head` became `2838c57dcc77`. Executing `downgrade("-1")` rolls back `2838c57dcc77` (dropping `outbox_events`), leaving `6c6aea5c9814` (`nid_number`) still applied. The hardcoded assertion expecting `nid_number` to be dropped failed.

---

## Resolution

1. **Transactional Outbox Pattern**:
   Store domain state and outbox events in the same local database transaction using a Unit of Work (`UnitOfWorkProtocol`). Either both persist atomically to disk, or neither does:
   ```python
   async with self._uow as uow:
       created_order = await uow.orders.create(order)
       outbox_event = OutboxEventEntity(
           aggregate_type="order",
           aggregate_id=created_order.id,
           event_type="OrderCreated",
           payload=payload,
       )
       await uow.outbox.save(outbox_event)
       await uow.commit()
   ```
   An asynchronous background worker (`OutboxRelayService`) polls `PENDING` records and dispatches them to Kafka with partition key = `aggregate_id`, ensuring guaranteed delivery and FIFO per entity.

2. **Updated Reversibility Test to Step Down the True Head**:
   The test was updated to verify that stepping down `"-1"` from the current `head` properly reverts `outbox_events` (verifying `outbox_events` table is dropped), and stepping down another step reverts `nid_number`:
   ```python
   # Step down from current head (outbox_events)
   command.downgrade(cfg, "-1")
   inspector = inspect(alembic_engine)
   assert "outbox_events" not in inspector.get_table_names()

   # Step down one more to verify nid_number reversibility
   command.downgrade(cfg, "-1")
   inspector = inspect(alembic_engine)
   columns = [col["name"] for col in inspector.get_columns("users")]
   assert "nid_number" not in columns
   ```

---

## Permanent Prevention Rule

- **Codified Rule**: Pattern #163 & #164 in `.agents/skills/fastapi-production/SKILL.md`.
- Anti-pattern #134 & #135 in `.agents/skills/fastapi-production/SKILL.md`.
- Whenever adding a new Alembic migration, always adapt `test_alembic_migrations.py` to assert rollback behavior for the new head migration first.
- Never write to message brokers directly inside primary domain service transactions without the Outbox Pattern.
