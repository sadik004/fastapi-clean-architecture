# RCA: Day 19 - N+1 Query Prevention, Defensive `lazy="raise"` & Multi-Revision Migration Reversibility

- **Date**: 2026-09-08
- **Role**: Junior Apprentice Backend Engineer
- **Lead Architect & Mentor**: User
- **Topic**: Relational Query Eager Loading, Async Greenlet Traps & Bi-directional Migration Downgrades

---

## 1. Trigger
During Day 19 implementation of the 1-to-many relationship (`UserModel` $\leftrightarrow$ `PostModel`):
1. Relationship traversal without eager loading caused synchronous I/O or `MissingGreenlet` exceptions in async mode.
2. During multi-revision migration testing in `test_alembic_migrations.py`, bi-directional reversibility (`downgrade base`) encountered constraint failures when dropping dependent tables in SQLite.
3. Iterating over parent collections triggered $N+1$ queries when naive relationship loading was utilized.

---

## 2. Faulty Code / Pattern

### Issue A: Implicit Lazy Loading in Async SQLAlchemy
```python
# Naive relationship definition
class UserModel(Base):
    posts: Mapped[list["PostModel"]] = relationship(back_populates="author")  # defaults to lazy="select"

# In async endpoint or service
users = await repo.list_users()
for u in users:
    print(len(u.posts))  # Triggers implicit synchronous SELECT or MissingGreenlet crash!
```
In asynchronous SQLAlchemy, accessing an un-loaded relationship at runtime attempts to execute synchronous I/O on an async event loop, raising `sqlalchemy.exc.MissingGreenlet: Greenlet_spawn has not been called; can't call a PGreenlet that is not running.`

### Issue B: Multi-Revision Downgrade Foreign Key Locks
```python
# In migration revision downgrade():
def downgrade() -> None:
    op.drop_table("users")  # ERROR: foreign key constraint failure because 'posts' references 'users'
    op.drop_table("posts")
```
Dropping tables out of dependency order or without cascading in SQLite or PostgreSQL triggers foreign key constraint violations.

---

## 3. Root Cause
1. **Implicit Lazy Loading Trap**: SQLAlchemy's historical default `lazy="select"` assumes a synchronous thread-per-request environment. In async architectures, lazy attribute access is strictly forbidden because coroutines cannot perform hidden, non-awaited I/O.
2. **Cartesian Product Bloat**: Using `joinedload` across 1-to-many collections creates Cartesian duplication in SQL result sets ($N \times M$ rows), multiplying network latency and heap memory consumption.
3. **DDL Reversal Ordering**: Migration scripts must strictly observe reverse topological sorting during `downgrade()`: child tables with foreign keys must be dropped before parent tables.

---

## 4. Resolution

### Solution A: Defensive `lazy="raise"` Invariant
Configured `lazy="raise"` on all relationship declarations:
```python
class UserModel(Base):
    posts: Mapped[list["PostModel"]] = relationship(
        back_populates="author",
        cascade="all, delete-orphan",
        lazy="raise",  # Compile/runtime guardrail
    )

class PostModel(Base):
    author: Mapped["UserModel"] = relationship(
        back_populates="posts",
        lazy="raise",
    )
```
Any attempt to access un-eagerly loaded relationships immediately raises `sqlalchemy.exc.InvalidRequestError`, exposing missing eager loaders during unit testing before hitting production.

### Solution B: Dual Eager Loading Strategy
1. **`selectinload` for 1-to-Many Collections**:
   ```python
   stmt = select(UserModel).options(selectinload(UserModel.posts))
   ```
   Emits strictly 2 queries: one for users, and one for all child posts using `WHERE posts.user_id IN (...)`.
2. **`joinedload` for Many-to-1 / 1-to-1 Scalars**:
   ```python
   stmt = select(PostModel).options(joinedload(PostModel.author))
   ```
   Emits strictly 1 query using a SQL `LEFT OUTER JOIN`.

### Solution C: Ordered Downgrade DDL
In revision `6bd9533b08d1_create_posts_table_and_relationship.py`:
```python
def downgrade() -> None:
    op.drop_index(op.f("ix_posts_user_id"), table_name="posts")
    op.drop_index(op.f("ix_posts_title"), table_name="posts")
    op.drop_table("posts")
```
Ensured reverse topological execution so child tables and indexes are cleanly unlinked before parent revisions are modified.

---

## 5. Permanent Prevention Rule
1. **Defensive `lazy="raise"` Standard**: Never define SQLAlchemy relationships without `lazy="raise"`.
2. **Strict Query Count Assertions**: Test all collection queries with `before_cursor_execute` SQL listeners to mathematically assert that loading $N$ parents with $M$ children emits exactly 2 queries.
3. **Bi-directional Migration CI Gate**: Every schema migration must pass bidirectional rollback testing (`upgrade head` $\to$ `downgrade -1` $\to$ `upgrade head` $\to$ `downgrade base`).
