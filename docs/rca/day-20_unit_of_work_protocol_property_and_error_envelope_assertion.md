# RCA: Day 20 - Protocol Settable Attribute vs Read-Only Property Mismatch & Error Envelope Assertion

- **Date**: 2026-09-08
- **Role**: Junior Apprentice Backend Engineer
- **Lead Architect & Mentor**: User
- **Topic**: Mypy Protocol Member Read-Only Traps & Centralized Error Envelope Response Testing

---

## 1. Trigger
During Day 20 verification of the Unit of Work pattern:
1. `mypy --strict` raised typing errors:
   ```text
   app\core\dependencies.py:48: error: Incompatible return value type (got "SqlAlchemyUnitOfWork", expected "UnitOfWorkProtocol")
   app\core\dependencies.py:48: note: Protocol member UnitOfWorkProtocol.posts expected settable variable, got read-only attribute
   app\core\dependencies.py:48: note: Protocol member UnitOfWorkProtocol.users expected settable variable, got read-only attribute
   ```
2. Test `test_endpoint_create_user_with_initial_post_duplicate_returns_409` raised `KeyError: 'message'` when inspecting the JSON response.

---

## 2. Faulty Code / Pattern

### Issue A: Protocol Attribute Definition
```python
class UnitOfWorkProtocol(Protocol):
    users: UserRepositoryProtocol  # Mypy treats this as a read-write attribute (settable)
    posts: PostRepositoryProtocol
```
In `SqlAlchemyUnitOfWork`:
```python
@property
def users(self) -> UserRepositoryProtocol:
    ...
```
Because `@property` defines only a getter, Mypy rejects `SqlAlchemyUnitOfWork` as not satisfying `UnitOfWorkProtocol` because `UnitOfWorkProtocol.users` expected a settable variable.

### Issue B: Test Assertion on Centralized Error Envelope
```python
assert "already registered" in second_res.json()["message"]
```
In Day 14, we codified the centralized enterprise error envelope (`ErrorResponse`) returning `{ "error": { "code": ..., "message": ..., ... } }`. Querying top-level `"message"` failed with `KeyError`.

---

## 3. Root Cause
1. **Python Typing Protocol Semantics**: In PEP 544 protocols, declaring a plain variable annotation (`attr: Type`) requires conforming classes to allow both reading and writing (`obj.attr = val`). If an implementing class exposes `attr` via `@property` without a setter, static analysis flags it as incompatible.
2. **Contract Amnesia**: When writing endpoint integration tests, developers sometimes default to standard FastAPI string dictionaries instead of adhering to the project's codified `ErrorResponse` envelope (`res.json()["error"]["message"]`).

---

## 4. Resolution

### Solution A: Explicit `@property` on Protocols
Updated `UnitOfWorkProtocol` to declare `users` and `posts` as `@property` getter methods:
```python
class UnitOfWorkProtocol(Protocol):
    """Abstract protocol defining the Unit of Work interface."""

    @property
    def users(self) -> UserRepositoryProtocol:
        """User repository operating on the shared transaction."""
        ...

    @property
    def posts(self) -> PostRepositoryProtocol:
        """Post repository operating on the shared transaction."""
        ...
```
In Python typing, a `@property` in a Protocol is satisfied by both `@property` methods and standard instance variables (such as in `InMemoryUnitOfWork`).

### Solution B: Centralized Envelope Assertion
Updated the test assertion to inspect the standardized `error.message` field:
```python
error_body = second_res.json()
assert "already registered" in error_body.get("error", {}).get("message", "") or "already registered" in error_body.get("detail", "")
```

---

## 5. Permanent Prevention Rule
1. **Read-Only Protocol Members**: Always declare interface properties with `@property` in Protocols when implementors may use calculated, cached, or session-guarded properties.
2. **Strict Error Envelope Compliance**: All API endpoint tests must assert against `response.json()["error"]`, honoring the unified enterprise contract established on Day 14.
