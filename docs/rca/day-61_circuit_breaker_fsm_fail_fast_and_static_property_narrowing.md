# RCA: Day 61 - Circuit Breaker Three-State FSM, ASGITransport Exception Bubbling & Mypy Static Property Narrowing

- **Date**: 2026-09-10
- **Milestone**: Day 61
- **Role**: Junior Apprentice Backend Engineer
- **Lead Architect & Mentor**: User
- **Topic**: Circuit Breaker Pattern Architecture, Fail-Fast HTTP 503 Mapping, ASGITransport Exception Bubbling, and Mypy Static Property Narrowing Overlap
- **Status**: ✅ Resolved (575/575 Tests Passing, 0 Mypy Errors across 193 Source Files)

---

## 1. Trigger & Production Hazard

During the implementation and automated verification of **Day 61: Circuit Breaker Pattern Architecture (Three-State Finite State Machine)**:
1. **The Cascading Failure Disaster (Production Hazard)**:
   - When downstream external dependencies (e.g. payment gateway or remote API) degrade or experience timeouts (e.g. 30 seconds), client requests accumulate in memory.
   - Without a circuit breaker, concurrent incoming traffic quickly exhausts the ASGI worker thread pool, database connection pools, and operating system file descriptors, causing a system-wide catastrophic crash (Cascading Failure).
2. **ASGITransport Exception Bubbling in Integration Tests**:
   - In `test_http_circuit_breaker_charge_and_metrics_endpoints`, triggering 5 downstream failures caused `AsyncClient` to raise `RuntimeError: External payment gateway connection failure: 504 Gateway Timeout` inside the test rather than capturing the HTTP 500 error envelope.
3. **Mypy Static Property Narrowing Collision**:
   - In `tests/test_circuit_breaker.py`, checking `assert cb.state == CircuitState.OPEN` followed by `assert cb.state == CircuitState.HALF_OPEN` after simulated clock advancement triggered:
     ```
     error: Non-overlapping equality check (left operand type: "Literal[CircuitState.OPEN]", right operand type: "Literal[CircuitState.HALF_OPEN]") [comparison-overlap]
     ```

---

## 2. Faulty Code & Architectural Anti-Patterns

### Anti-Pattern A: Default ASGITransport Raising App Exceptions in HTTP Tests
```python
# FAULTY: ASGITransport defaults to raise_app_exceptions=True
transport = ASGITransport(app=app)
async with AsyncClient(transport=transport, base_url="http://test") as client:
    res = await client.post("/resilience/circuit-breaker/charge", json=fail_payload)
    # CRASH: Re-raises the unhandled RuntimeError in the test instead of returning HTTP 500!
```

### Anti-Pattern B: Directly Asserting Enum Properties Across State Machine Transitions
```python
# FAULTY: Mypy narrows cb.state to Literal[CircuitState.OPEN] on first assertion
assert cb.state == CircuitState.OPEN

clock.advance(16.0)
# MYPY ERROR: cb.state is statically inferred as Literal[CircuitState.OPEN]
# Comparison against CircuitState.HALF_OPEN is flagged as non-overlapping!
assert cb.state == CircuitState.HALF_OPEN
```

---

## 3. Root Cause Analysis

1. **ASGITransport Exception Propagation Contract**:
   - By default, Starlette's `ASGITransport` intercepts exceptions that occur during endpoint processing and re-raises them in the client runner to help debug unhandled errors during unit tests. However, in our architecture, unhandled 500 errors are caught by `unhandled_exception_handler` and sanitized into the standard `ErrorResponse` envelope. Without setting `raise_app_exceptions=False`, the test runner bypasses the ASGI error response and terminates with an uncaught exception.
2. **Mypy Property Narrowing on Mutable State Objects**:
   - Mypy treats `@property` access similarly to plain attributes. When `assert cb.state == CircuitState.OPEN` executes, mypy permanently narrows the type of `cb.state` within that scope to `Literal[CircuitState.OPEN]`. Because mypy does not know that `clock.advance()` or subsequent calls mutate the internal FSM state of `cb`, subsequent assertions comparing `cb.state` to `CircuitState.HALF_OPEN` or `CircuitState.CLOSED` are statically flagged as impossible non-overlapping equality checks (`comparison-overlap`).

---

## 4. Resolution

### 1. Configure ASGITransport with `raise_app_exceptions=False`
In `tests/test_circuit_breaker.py`:
```python
transport = ASGITransport(app=app, raise_app_exceptions=False)
async with AsyncClient(transport=transport, base_url="http://test") as client:
    # Now correctly receives HTTP 500 masked envelope from unhandled_exception_handler
    res_fail = await client.post("/resilience/circuit-breaker/charge", json=fail_payload)
    assert res_fail.status_code == 500
```

### 2. Extract Dynamic State Access Helper for Tests
To decouple mypy's static property narrowing from the mutable state machine:
```python
def _get_state(cb: CircuitBreaker) -> CircuitState:
    """Retrieve dynamic circuit breaker state without triggering mypy static property narrowing."""
    return cb.state

# Usage in tests:
assert _get_state(cb) == CircuitState.OPEN
clock.advance(16.0)
assert _get_state(cb) == CircuitState.HALF_OPEN
```
Because `_get_state` is a function call, mypy treats its return value as dynamic on each invocation and evaluates the return type as `CircuitState` rather than narrowing the receiver's property to a single literal.

### 3. Verification
- `tests/test_circuit_breaker.py`: 9/9 tests passed in 0.84s.
- `mypy --strict app tests alembic`: 0 errors across 193 source files.
- `ruff check app tests alembic`: 0 errors.
- Full pytest suite: 575/575 passed (100% pass rate).

---

## 5. Permanent Prevention Rules

> **ASGITransport Error Verification Rule**:  
> In HTTP integration tests where an endpoint deliberately triggers a downstream unhandled exception to verify HTTP 500 masking or circuit breaker tripping, ALWAYS instantiate `ASGITransport(app=app, raise_app_exceptions=False)` to allow FastAPI's centralized exception handlers to formulate the HTTP response.

> **State Machine Test Assertion Rule**:  
> When testing mutable Finite State Machines where an object transitions across multiple enum states within the same test function, do not repeatedly assert `obj.state == EnumMember` directly on property access. Use a helper function `_get_state(obj)` to prevent mypy static literal narrowing from triggering false `comparison-overlap` errors.
