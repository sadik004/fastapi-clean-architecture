# Day 79: Static Security Audits & Code Hardening Architecture (AST Security Analysis with Bandit & Semgrep)

## Overview
Engineered an enterprise-grade Static Application Security Testing (SAST) and Abstract Syntax Tree (AST) code hardening architecture using **Bandit** and **Semgrep** to systematically detect OWASP Top 10 vulnerabilities (CWE-89 SQLi, CWE-798 Hardcoded Secrets, CWE-338 Insecure PRNG, CWE-94 Code Injection, CWE-502 Deserialization) and strictly enforce 4 custom architectural invariants across the unified FastAPI codebase.

---

## Architectural Invariants & Enforced Rules

1. **Rule 1: Zero Raw SQL in Routers (`fastapi-no-raw-sql-in-routers`)**
   - **CWE-89 / Architectural Boundary**: Detects calls to `session.execute(text(...))` inside `app/routers/`.
   - **Remediation**: All SQL interactions must be encapsulated inside repository abstractions (`app/repositories/`), enforcing 3-tier clean architecture separation and enabling transparent read/write replica query routing.

2. **Rule 2: Zero Insecure PRNG in Production (`fastapi-no-insecure-random`)**
   - **CWE-338**: Standard library `random` uses the Mersenne Twister algorithm, whose internal state can be fully reconstructed after observing 624 32-bit outputs.
   - **Remediation**: For security tokens, password resets, and session identifiers, developers must use the cryptographically secure `secrets` module (`secrets.token_urlsafe`, `secrets.randbelow`). Non-cryptographic backoff jitter in `backoff.py` is safely excluded.

3. **Rule 3: Zero `print()` Statements in Production (`fastapi-no-print-in-production`)**
   - **CWE-778**: Unstructured `print()` statements leak memory, bypass log redaction processors, and cannot be parsed or indexed by centralized observability pipelines (Datadog, Grafana Loki, ELK).
   - **Remediation**: All logging must route through `structlog` (`logger.info()`, `logger.error()`) with structured JSON formatting and correlation IDs.

4. **Rule 4: Zero Dangerous Code Execution (`fastapi-no-eval-exec`)**
   - **CWE-94 / CWE-502**: Use of built-ins `eval()`, `exec()`, and insecure deserialization (`pickle.loads()`) allows arbitrary Remote Code Execution (RCE).
   - **Remediation**: Evaluators and parsers must strictly rely on type-safe deserialization (e.g. `ast.literal_eval()`, `json.loads()`, Pydantic models).

5. **Rule 5: Zero Hardcoded Secrets (`fastapi-no-hardcoded-secrets`)**
   - **CWE-798**: Hardcoded API keys, JWT secrets, and database credentials committed into source code result in catastrophic credential exposure.
   - **Remediation**: All secrets must be injected dynamically from environment variables into Pydantic `BaseSettings` (`SecretStr`).

---

## Core Components Implemented

### 1. Automated SAST Configuration Files
- **`.bandit`**: Configured profile targeting `app/`, excluding test suites (`tests/`), database migrations (`alembic/`), documentation, and scripts, enforcing zero High and Medium issues (`bandit -r app -ll`).
- **`.semgrep.yml` & `deployments/security/semgrep_rules.yml`**: Declarative Semgrep rule definitions with unanchored paths (`**/app/routers/**`, `**/app/**`) enforcing the architectural policies.

### 2. `app/services/security_audit_service.py`
- Implemented `SecurityAuditService`:
  - `audit_code_snippet(code, filename, is_router)`: In-memory AST analysis for sub-millisecond unit test validation.
  - `run_ast_audit(target_dir)`: $\mathcal{O}(N_{\text{nodes}})$ recursive AST scanner iterating across Python syntax trees using `ast.NodeVisitor`.
  - `run_bandit_audit(target_dir)`: Programmatic execution of Bandit via `subprocess.run(["bandit", "-q", "-r", ..., "-f", "json", "-ll"])` with robust JSON extraction.
  - `get_security_summary(target_dir)`: Aggregated compliance report producing immutable `SecurityAuditReport` records with status (`SECURE` vs `VULNERABLE`).
  - `get_security_audit_service()`: Singleton dependency provider.

### 3. `app/routers/security_audit_router.py`
- Mounted under prefix `/observability/security` with OpenAPI tags:
  - `GET /observability/security/audit-summary`: Returns high-level compliance metrics (`status: "SECURE"`, file/line counts, issue counts).
  - `GET /observability/security/audit-details`: Returns comprehensive report with itemized findings, severity levels, line numbers, and CWE references.

### 4. `app/main.py`
- Registered `security_audit_router` directly into the FastAPI application.

---

## Verification & Automated Tests
Authored comprehensive test suite `tests/test_static_security_audit.py`:
- `test_bandit_zero_high_medium_in_app`: Asserts zero High and zero Medium Bandit issues across `app/`.
- `test_ast_rule_catches_raw_sql_in_router`: Validates detection of `session.execute(text(...))` in routers.
- `test_ast_rule_permits_raw_sql_in_repositories`: Verifies clean architecture compliance allowing raw queries in repository layers.
- `test_ast_rule_catches_insecure_random`: Validates detection of weak PRNG (`random.randint`).
- `test_ast_rule_catches_eval_exec`: Validates detection of `eval()` code injection.
- `test_ast_rule_catches_print`: Validates detection of un-structured `print()` calls.
- `test_ast_rule_catches_hardcoded_secrets`: Validates detection of literal API keys.
- `test_clean_codebase_zero_ast_violations`: Proves entire `app/` codebase is 100% compliant (0 violations).
- `test_security_audit_summary_api`: Verifies HTTP 200 and `status: "SECURE"` on summary endpoint.
- `test_security_audit_details_api`: Verifies itemized issue structure on details endpoint.

---

## Quality & Compliance Gates Passed
- **Bandit SAST**: `bandit -r app -ll` $\to$ **0 High, 0 Medium** issues across 18,940 lines of code.
- **Semgrep SAST**: `semgrep scan --config=.semgrep.yml app` $\to$ **0 findings** across 165 files.
- **Unit & Integration Tests**: `pytest tests/test_static_security_audit.py -v` $\to$ **10/10 passed**.
- **Cross-Service Regression**: `pytest tests/test_database_replica_splitting.py tests/test_graceful_shutdown.py -v` $\to$ **18/18 passed**.
- **Static Type Safety**: `mypy --strict app/routers/security_audit_router.py app/services/security_audit_service.py tests/test_static_security_audit.py` $\to$ **0 issues**.
- **Code Style & Formatting**: `ruff check app tests alembic` $\to$ **All checks passed**.
