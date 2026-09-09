# RCA: Day 33 - Test Assertion Schema Alignment, Initial State DB Bypass & Admin Auth Seeding in Write Caching Architectures

- **Date**: 2026-09-09
- **Role**: Junior Apprentice Backend Engineer
- **Lead Architect & Mentor**: User
- **Topic**: DTO Response Schema Alignment, Write-Side Lookup DB vs Cache Isolation, Admin User Seeding in Integration Fixtures, and `ruff` Import Ordering under Strict Pre-Commit Standards.

---

## 1. Trigger & Incident Scenario

During the automated verification of Day 33's Advanced Caching Architectures (Write-Through and Write-Behind):

### Incident 1: Initial Test Failure on Write-Through Cache Metrics Assertion
In `test_write_through_updates_db_and_warms_cache`, after performing a write-through update and then fetching the user by ID, the test asserted:
```python
metrics = await cache_service.get_metrics()
assert metrics["hits"] == 1
assert metrics["misses"] == 0
```
However, pytest failed with:
```text
FAILED tests/test_write_patterns.py::test_write_through_updates_db_and_warms_cache - assert 1 == 0
```
Telemetry reported `misses: 1, hits: 1`.

### Incident 2: Integration Test HTTP 401 Unauthorized
In `test_http_write_through_and_write_behind_endpoints`, the HTTP PUT request to `/users/{user_id}/write-through` with `admin_auth_headers` returned HTTP 401 Unauthorized:
```text
WARNING app.exception_handlers: HTTP exception [401 UNAUTHORIZED] at '/users/1/write-through': Invalid authentication credentials
FAILED tests/test_write_patterns.py::test_http_write_through_and_write_behind_endpoints - assert 401 == 200
```

### Incident 3: Test Assertion Divergence on Status & Flush Keys
In `test_write_behind_batch_flusher`, the test asserted `flush_result["synced_users_count"] == 2`, but `AnalyticsService.sync_pending_views_to_db` returned schema fields `flushed_records` and `total_views`, raising `KeyError: 'synced_users_count'`. Similarly, `record_view` returned status `"recorded"` while the test expected `"buffered"`.

---

## 2. Root Cause Analysis

### A. Root Cause of Write-Through Cache Miss Metric
1. In `UserService.update_user_write_through`, the first line was:
   ```python
   user = await self.get_user_by_id(user_id)
   ```
2. Because this was a brand-new user whose profile had never been fetched via GET before, `self.get_user_by_id(user_id)` evaluated `CacheService.get_str(key)`, found no cached entry, and recorded **1 cache miss**.
3. While functionally benign, in a write operation that subsequently hydrates the cache, querying the cache beforehand caused an unnecessary cache miss metric to be counted.
4. **Resolution**: In `update_user_write_through` (and `update_user`), look up the target entity directly from the underlying database repository (`await self._repo.get_by_id(user_id)`). This guarantees that write mutations never contaminate cache miss telemetry.

### B. Root Cause of HTTP 401 on Admin Auth Headers
1. In our security dependency `get_current_user`, when an API key matches `settings.admin_api_key`, the system retrieves the administrator entity from the repository using `service.get_user_by_username(username=settings.admin_username)`.
2. In `conftest.py`, the `admin_auth_headers` fixture provides the header `{"X-API-Key": settings.admin_api_key}`, but the database table/in-memory store did not contain the admin user record until the `admin_user` fixture was explicitly injected into the test function.
3. Because the test parameter list did not include `admin_user`, the user lookup failed with `UserNotFoundException`, resulting in HTTP 401.
4. **Resolution**: Explicitly inject the `admin_user: dict[str, Any]` fixture into any test that uses `admin_auth_headers`.

### C. Root Cause of DTO Field Name Inconsistency
1. The test code was drafted with tentative field names (`synced_users_count`, `buffered`), whereas the formal Pydantic models `ViewsFlushResponse` and `UserViewResponse` in `app/schemas/metrics.py` established:
   - `flushed_records`: count of unique user IDs whose view totals were flushed.
   - `total_views`: aggregate count of all views flushed in the batch.
   - `status`: `"recorded"` with `mode`: `"write-behind"`.
2. **Resolution**: Aligned test assertions strictly with the typed Pydantic models.

---

## 3. Corrected Implementations

### Direct DB Lookup in `UserService.update_user_write_through`:
```python
# app/services/user_service.py
user = await self._repo.get_by_id(user_id)
if user is None:
    raise UserNotFoundException(user_id=user_id)
```

### Injected `admin_user` in HTTP Tests:
```python
# tests/test_write_patterns.py
@pytest.mark.asyncio
async def test_http_write_through_and_write_behind_endpoints(
    fake_redis: Any,
    admin_user: dict[str, Any],
    admin_auth_headers: dict[str, str],
) -> None:
    ...
```

---

## 4. Permanent Prevention Rules

1. **Write Operations Must Not Query Cache for Existence**: When validating pre-existing entities during write/update workflows (`update_user`, `update_user_write_through`), always query `_repo.get_by_id(user_id)` directly to avoid recording misleading cache misses.
2. **Always Pair Auth Header Fixtures with Auth User Seed Fixtures**: Any test utilizing `admin_auth_headers` must also declare `admin_user` in its fixture arguments so that the authentication guard resolves the user record without HTTP 401 errors.
3. **Contract-Driven Test Assertions**: Tests must import and assert against the exact fields declared in the domain schema DTOs (`ViewsFlushResponse`, `UserViewResponse`).
