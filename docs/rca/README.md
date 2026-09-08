# Root Cause Analysis (RCA) Directory

This directory logs all architecture traps, test failures, performance bottlenecks, and corrections made during our 90-Day curriculum.

---

## Complete RCA Incident Catalog

| Milestone | Topic | Primary Root Cause & Resolution |
| :--- | :--- | :--- |
| **Day 01** | [3-Tier Coupling & Linear Scan Trap](day-01_architecture_boundaries_and_domain_exceptions.md) | Decoupled domain exceptions from HTTP transport; implemented inverted hash indexes to avoid $\mathcal{O}(n)$ scans. |
| **Day 02** | [Credential Leakage & DTO Boundaries](day-02_pydantic_v2_field_constraints_and_dto_separation.md) | Segregated request/response DTOs; eliminated mutable default arguments via `Field(default_factory=...)`. |
| **Day 03** | [Index Desynchronization & Ghost Keys](day-03_repository_index_synchronization_and_ghost_keys.md) | Strictly purged inverted indexes on deletion and synchronized keys on entity updates to eliminate phantom collisions. |
| **Day 04** | [Regex Recompilation & List Scans](day-04_field_validators_regex_recompilation_and_list_lookup.md) | Pre-compiled regular expressions at module scope; replaced $\mathcal{O}(m)$ list scans with $\mathcal{O}(1)$ `frozenset` lookups. |
| **Day 05** | [model_dump() Allocation & Transient Leaks](day-05_model_validator_dump_allocation_and_transient_leakage.md) | Replaced `self.model_dump()` with direct attribute access in `@model_validator`; isolated transient validation fields. |
| **Day 06** | [Path/Query Validation & OpenAPI Traps](day-06_path_query_validation_and_openapi.md) | Polymorphic `UserNotFoundException`; OpenAPI 3.1 `anyOf` schema awareness; DTO update responsibility boundaries. |

---

## RCA Entry Format

Each file is named `day-XX_<topic>.md` and follows this structure:

```markdown
# RCA: [Title of the Error / Bug]

- **Trigger**: [Test failure / Lead Architect feedback / Runtime error]
- **Faulty Code / Pattern**:
  ```python
  # Code that failed
  ```
- **Root Cause**:
  Detailed explanation of why this was flawed or sub-optimal.
- **Resolution**:
  ```python
  # Corrected implementation
  ```
- **Permanent Prevention Rule**:
  Rule added to .agents/skills/fastapi-production/SKILL.md to ensure zero recurrence.
```
