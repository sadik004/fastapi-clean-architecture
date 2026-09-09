# Day 45: High-Performance Bitmasking RBAC Architecture (O(1) Bitwise Permission Checking)

## 1. Overview & Architectural Objectives
Traditional Role-Based Access Control (RBAC) relies on relational database multi-table joins (`users JOIN user_roles JOIN roles JOIN role_permissions JOIN permissions`), causing massive database CPU pressure, connection starvation, and latency spikes in high-concurrency microservices and real-time platforms (e.g. Discord, Slack, Linux).

Day 45 implements an enterprise-grade **Bitmasking Role-Based Access Control (Bitmasking RBAC)** engine:
1. **Binary Bitwise Flags ($\mathcal{O}(1)$ Single-Cycle CPU Math)**:
   - Permissions are defined as integer bitwise flags using powers of 2 ($2^n$) via Python's `enum.IntFlag`:
     - `READ = 1 << 0` (1)
     - `WRITE = 1 << 1` (2)
     - `DELETE = 1 << 2` (4)
     - `ADMIN = 1 << 3` (8)
     - `EXPORT = 1 << 4` (16)
     - `BILLING = 1 << 5` (32)
   - Zero-database-join permission checking executes strictly via:
     `has_permission(user_perms, required) = (user_perms & required) == required`
2. **Dynamic Composite Roles**:
   - Roles are bitwise OR (`|`) compositions:
     - `ROLE_GUEST = 1` (READ)
     - `ROLE_USER = 3` (READ | WRITE)
     - `ROLE_MODERATOR = 7` (READ | WRITE | DELETE)
     - `ROLE_ADMIN = 63` (READ | WRITE | DELETE | ADMIN | EXPORT | BILLING)
3. **Extreme Memory & Storage Efficiency**:
   - Stores up to 64 independent permissions in a single 64-bit integer column in PostgreSQL/SQLite.
   - Eliminates dozens of redundant boolean columns and intermediary join mapping rows.
4. **Declarative FastAPI Guard**:
   - Declarative dependency `require_permission(Permission.DELETE)` inspects authenticated user bitmask claims in-memory without initiating database roundtrips.
   - Rejects unauthorized requests immediately with `HTTP 403 Forbidden` and descriptive details.
5. **Administrative Permission Mutation**:
   - Endpoints allowing administrators to dynamically grant (`|`), revoke (`& ~`), or override bitmask permissions on target users with immediate propagation.

---

## 2. Bitwise Mechanics & State Transition

```
Binary Bitwise Masking Operations:
=================================

1. CHECK: Has DELETE (4)?
   User Perms (ROLE_USER = 3):    0 0 0 0 0 0 1 1
   Required (DELETE = 4):       & 0 0 0 0 0 1 0 0
   ----------------------------------------------
   Result:                        0 0 0 0 0 0 0 0 != 4 -> FALSE (HTTP 403)

   User Perms (ROLE_MOD = 7):     0 0 0 0 0 1 1 1
   Required (DELETE = 4):       & 0 0 0 0 0 1 0 0
   ----------------------------------------------
   Result:                        0 0 0 0 0 1 0 0 == 4 -> TRUE (ALLOW 204)

2. GRANT: Add DELETE (4) to ROLE_USER (3) -> ROLE_MODERATOR (7)
   User Perms (3):                0 0 0 0 0 0 1 1
   Grant Flag (4):              | 0 0 0 0 0 1 0 0
   ----------------------------------------------
   New Perms:                     0 0 0 0 0 1 1 1 (7)

3. REVOKE: Remove DELETE (4) from ROLE_MODERATOR (7) -> ROLE_USER (3)
   User Perms (7):                0 0 0 0 0 1 1 1
   NOT Flag (~4):               & 1 1 1 1 1 0 1 1
   ----------------------------------------------
   New Perms:                     0 0 0 0 0 0 1 1 (3)
```

---

## 3. Implementation Summary

### 3.1 Core Permission Flags (`app/core/permissions.py`)
- Defines `Permission(IntFlag)` with powers of 2.
- Defines composite roles `ROLE_GUEST`, `ROLE_USER`, `ROLE_MODERATOR`, `ROLE_ADMIN`.
- Implements pure functions `has_permission`, `grant_permission`, `revoke_permission`, and `get_permission_names`.

### 3.2 Database Model & Migration (`app/models/user.py`)
- Added `permissions: Mapped[int] = mapped_column(Integer, default=3, server_default="3", nullable=False)`.
- Generated and executed Alembic migration `9fcf16ef83c7_add_permissions_bitmask_to_users.py`.

### 3.3 Domain Entities & Schemas
- Updated `UserEntity` and `UserResponse` with `permissions: int` and computed property `permission_names: list[str]`.
- Updated `AuthenticatedUserResponse` with `permissions: int`.
- Defined `UpdateUserPermissionsRequest` supporting `permissions`, `grant`, and `revoke` fields.

### 3.4 Declarative Guards & Router (`app/core/dependencies.py` & `app/routers/user_router.py`)
- Implemented `PermissionGuard` and factory `require_permission`.
- Protected `DELETE /users/{user_id}` with `require_permission(Permission.DELETE)`.
- Protected `GET /users/admin/analytics` with `require_permission(Permission.ADMIN)`.
- Added `PUT /users/{user_id}/permissions` allowing administrators to mutate bitmasks dynamically.

---

## 4. Verification Results
- `tests/test_bitmasking_rbac.py`:
  - `test_bitwise_flag_mathematics`: PASSED
  - `test_forbidden_rejection_http_403`: PASSED
  - `test_authorized_access_http_200_or_204`: PASSED
  - `test_dynamic_permission_mutation`: PASSED
- Full Test Suite Regression: 449 passed in 49.90s (100% pass rate).
- Type Safety: `mypy --strict app tests alembic` passed cleanly with 0 errors across 116 files.
- Linter: `ruff check app tests alembic` passed cleanly with 0 errors.
