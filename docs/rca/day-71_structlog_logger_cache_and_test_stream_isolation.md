# Root Cause Analysis (RCA): Day 71 - Structlog Logger Cache Drift & Test Stream Isolation

## 1. Executive Summary

- **Incident Classification**: Observability Infrastructure & Test Suite State Isolation
- **Severity**: Medium (Full Test Suite Inter-Test State Pollution)
- **Primary Failure Mode**: Module-Level Structlog Proxy Logger Caching Initial Configuration Across Test File Boundaries
- **Component Under Analysis**: `app/core/logging.py`, `app/core/middleware.py`, `tests/test_structured_logging.py`
- **Resolution**: Added `structlog.reset_defaults()` and disabled eager wrapper caching via `cache_logger_on_first_use=False`, converted test logging fixture into a state-restoring generator fixture.

---

## 2. Problem Statement & Symptoms

During the execution of the full regression test suite (`pytest -q` across 680 tests):
1. **Targeted Execution Passed**: Running `pytest tests/test_structured_logging.py -v` independently produced an immaculate 100% pass rate (11/11 tests passing).
2. **Full Regression Suite Failed**: When executed as part of the full test suite in alphabetical order, `test_correlation_id_propagation_via_middleware` and `test_correlation_id_auto_generation_uuidv7` failed with:
   ```
   > assert len(app_records) >= 2
   E assert 0 >= 2
   E + where 0 = len([])
   ```
3. **Empty Capture Buffer**: The in-memory `io.StringIO()` buffer attached to standard library logging handlers in `tests/test_structured_logging.py` contained 0 records emitted by `CustomSecurityAndObservabilityMiddleware`.

---

## 3. Root Cause Analysis (5 Whys)

1. **Why did `_get_log_records(buffer)` return zero records for HTTP middleware logs?**  
   Because the middleware's logger (`logger = get_logger("app.middleware")`) did not write its log records to the test fixture's in-memory `io.StringIO()` stream handler.
2. **Why was the middleware logger not writing to the test handler?**  
   Because the middleware logger was still bound to the initial standard output stream handler initialized during application module import in `app/main.py`.
3. **Why did the middleware logger remain bound to the old stream handler instead of the test handler?**  
   Because `setup_logging` was configured with `cache_logger_on_first_use=True`. When preceding tests (such as `test_abac_policy_engine.py`) dispatched the very first HTTP request, `BoundLoggerLazyProxy` resolved and permanently cached its wrapped logger pipeline.
4. **Why didn't calling `setup_logging(force_json=True)` in `test_structured_logging.py` update the cached logger?**  
   Because `structlog.configure()` preserves previously cached loggers unless `structlog.reset_defaults()` is explicitly invoked to invalidate the internal cache.
5. **How do we permanently solve this?**  
   1. Invoke `structlog.reset_defaults()` at the beginning of `setup_logging()`.  
   2. Configure `cache_logger_on_first_use=False` so that bound loggers dynamically evaluate the active processor chain and handler topology without retaining stale references.  
   3. Update `configure_test_logging` to be a generator fixture that restores previous logging handlers upon completion.

---

## 4. Architectural Solution & Implementation

### 4.1 Invalidate Cache & Dynamic Evaluation (`app/core/logging.py`)
```python
def setup_logging(
    environment: str = "production",
    log_level: int = logging.INFO,
    force_json: bool = False,
) -> None:
    # 1. Purge cached proxy bindings and reset configuration defaults
    structlog.reset_defaults()
    shared_processors, renderer = get_processors(environment=environment, force_json=force_json)

    # 2. Disable logger method caching to allow dynamic reconfiguration
    structlog.configure(
        processors=shared_processors + [
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=False,
    )
```

### 4.2 State Restoration Generator Fixture (`tests/test_structured_logging.py`)
```python
@pytest.fixture(autouse=True)
def configure_test_logging() -> Generator[tuple[io.StringIO, logging.Handler], None, None]:
    buffer = io.StringIO()
    setup_logging(environment="production", force_json=True)

    root_logger = logging.getLogger()
    old_handlers = list(root_logger.handlers)
    old_level = root_logger.level

    handler = logging.StreamHandler(buffer)
    if root_logger.handlers:
        handler.setFormatter(root_logger.handlers[0].formatter)
    root_logger.handlers = [handler]
    root_logger.setLevel(logging.DEBUG)

    yield buffer, handler

    # Restore previous root logger handlers and log level
    root_logger.handlers = old_handlers
    root_logger.setLevel(old_level)
```

---

## 5. Prevention Rules & Invariants

1. **Explicit Reset Invariant**: Whenever re-configuring `structlog` in dynamic environments or test harnesses, always call `structlog.reset_defaults()` prior to `structlog.configure()`.
2. **Zero Stale Proxy Invariant**: Avoid setting `cache_logger_on_first_use=True` when application components hold module-level logger references that may experience runtime logging topology changes.
3. **Generator Fixture Hygiene**: Any pytest fixture modifying standard library `root_logger.handlers` must yield and restore the original handlers in its teardown phase.
