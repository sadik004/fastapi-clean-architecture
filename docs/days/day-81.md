# Day 81: Architecture Compliance Test Gates Architecture (Enforcing Clean Layering, Inward Dependency Rules & Anti-Leak Policies via Automated Tests)

## Overview
Engineered an enterprise-grade **Architecture Compliance Testing & Layer Boundary Enforcement Engine** using Python Abstract Syntax Tree (`ast.parse()`) static analysis, directed module graph $G = (V, E)$ modeling, and Depth-First Search (DFS) cycle detection. This suite programmatically asserts Clean Architecture boundaries, enforces the Inward Dependency Rule, isolates HTTP transport mechanics from domain services, verifies repository dependency inversion, and eliminates circular import cycles across all 170+ modules in the codebase.

---

## 5 Canonical Architecture Rules Enforced

1. **Rule 1: Inward Boundary (Routers Never Import Database Models)**:
   - Asserts zero modules in `app/routers/` import from `app.models` (Strict ORM shielding).
   - Routers operate strictly on request payloads and response projection DTOs (`app/schemas/`), delegating persistence and entity hydration to services and repositories.

2. **Rule 2: Transport Isolation (Services Never Import FastAPI Transport)**:
   - Asserts no file in `app/services/` imports `fastapi.Request`, `fastapi.Response`, `fastapi.APIRouter`, status codes, or `fastapi.HTTPException`.
   - Services remain 100% protocol- and transport-agnostic, raising domain exceptions (`AuthenticationException`, `EntityNotFoundException`, etc.) that centralized exception handlers map to HTTP error envelopes.

3. **Rule 3: Dependency Inversion (Services Inject Protocols, Not Concrete Repos)**:
   - Asserts service constructors taking persistence dependencies reference abstract Protocol interfaces (`*Protocol` or `app.core.protocols`), never concrete implementation classes (`SqlAlchemy*`, `InMemory*`).

4. **Rule 4: Circular Dependency Elimination (Strict DAG Invariant)**:
   - Models the module graph $G = (V, E)$ where vertices are modules and edges are import statements.
   - Executes Depth-First Search (DFS) three-color cycle detection with $\mathcal{O}(V + E)$ time complexity.
   - Asserts the graph is strictly a Directed Acyclic Graph (DAG) with **0 circular dependency cycles**.

5. **Rule 5: Schema Autonomy (DTO Schemas Strictly Housed in app/schemas/)**:
   - Asserts routers and services never declare Pydantic `BaseModel` or SQLAlchemy `Base` classes inline.
   - All contract schemas reside cleanly within the centralized `app/schemas/` package.

---

## Core Components Implemented

### 1. Core Architecture Linter Engine (`app/core/architecture_linter.py`)
- `ArchitectureViolation`: Dataclass capturing rule ID, rule name, file path, line number, and violation details.
- `ArchitectureLinter`:
  - `index()`: Discovers all Python source files in `app/`, maps module namespaces, parses ASTs, and extracts `ast.Import` and `ast.ImportFrom` nodes.
  - `detect_cycles()`: Three-color DFS cycle detector in $\mathcal{O}(V + E)$ time (< 350ms across 170+ modules and 1,700+ imports).
  - Distinguishes runtime imports from static typing annotations under `if TYPE_CHECKING:` guards to prevent false-positive cycle flags.
  - `check_rule_1_inward_boundary()`, `check_rule_2_transport_isolation()`, `check_rule_3_dependency_inversion()`, `check_rule_4_strict_dag()`, `check_rule_5_schema_autonomy()`, and `check_all()`.
  - `audit_code_snippet()`: In-memory AST auditor for negative-control test gates.

### 2. Consolidated Protocol Registry (`app/core/protocols.py`)
- Unifies and re-exports canonical repository and persistence interfaces:
  - `UserRepositoryProtocol`, `ProductRepositoryProtocol`, `OrderRepositoryProtocol`, `OutboxRepositoryProtocol`, `DocumentRepositoryProtocol`, `PostRepositoryProtocol`, `CatalogRepositoryProtocol`, and `UnitOfWorkProtocol`.

### 3. Decoupled Authentication Exceptions (`app/core/exceptions.py` & `app/core/exception_handlers.py`)
- Introduced `AuthenticationException(BaseDomainException)` mapping to HTTP 401 with standard `WWW-Authenticate: Bearer` response headers.
- Replaced all raw `HTTPException` raises in `app/services/auth_service.py` with pure domain exceptions.

### 4. CLI Architecture Auditor Tool (`scripts/audit_architecture.py`)
- Standalone command-line utility for developers and CI/CD pipelines.
- Formats and displays an ASCII audit table containing module count ($V$), internal edges ($E$), total AST import nodes, cyclomatic status, and itemized rule compliance results.
- Returns exit code `0` on 100% clean architecture and `1` on boundary breach.

### 5. Automated Pytest Test Suite (`tests/test_architecture_compliance.py`)
- 10 automated test cases:
  1. `test_rule_1_inward_layer_boundary_zero_model_leakage`: Verifies 0 model imports in routers.
  2. `test_rule_2_transport_layer_isolation_zero_fastapi_coupling`: Verifies 0 FastAPI transport imports in services.
  3. `test_rule_3_dependency_inversion_services_inject_protocols`: Verifies protocol injection in services.
  4. `test_rule_4_module_graph_acyclic_invariant_strict_dag`: Asserts exactly 0 circular dependency cycles.
  5. `test_rule_5_schema_autonomy_dtos_housed_in_schemas_package`: Verifies 0 inline schemas in routers/services.
  6. `test_full_architecture_compliance_audit_passes`: Verifies consolidated check yields 0 violations.
  7. `test_negative_control_detects_intentional_inward_boundary_violation`: Negative control for Rule 1.
  8. `test_negative_control_detects_intentional_transport_isolation_violation`: Negative control for Rule 2.
  9. `test_negative_control_detects_intentional_inline_dto_violation`: Negative control for Rule 5.
  10. `test_cli_architecture_audit_script_execution`: Subprocess execution of `scripts/audit_architecture.py` asserting exit code 0.

---

## Architectural Refactorings & Drift Remediation

1. **`app/routers/catalog_router.py`**: Removed unused `from app.models.catalog_item import CatalogItemModel` import.
2. **`app/services/cache_service.py`**: Removed unused `from fastapi import Depends` import and moved `get_cache_service` provider to `app/core/dependencies.py`.
3. **`app/services/auth_service.py`**: Replaced `HTTPException` and `status` with domain `AuthenticationException` and `AuthorizationException`.
4. **`app/services/product_service.py`**: Refactored to inject `ProductRepositoryProtocol` and `UnitOfWorkProtocol` instead of concrete `SqlAlchemyProductRepository` and `ProductModel`.
5. **`app/repositories/user_repository.py` & `app/repositories/post_repository.py`**: Eliminated cross-repository circular imports by mapping domain `UserEntity` directly from model attributes.
6. **`app/schemas/`**: Extracted inline schemas to dedicated modules (`profiling.py`, `security_audit.py`, `security.py`, `tracing.py`).

---

## Verification Results

- `scripts/audit_architecture.py`:
  - Modules Audited ($V$): 173
  - Internal Import Dependencies ($E$): 370
  - Total AST Nodes Walked: 1,701
  - Graph Cyclomatic Status: Strict DAG (0 Cycles)
  - Duration: ~311 ms
  - Result: Exit Code 0 (PASS)
- `tests/test_architecture_compliance.py`: 10 passed in 1.77s.
- `mypy --strict`: Success (0 issues across all files).
- `ruff check`: All checks passed.
