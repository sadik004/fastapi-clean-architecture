---
name: fastapi-production
description: Master skill for production-grade FastAPI backend engineering following 3-tier Clean Architecture, strict DSA optimization, single evolving codebase, and continuous error learning. Triggers on all FastAPI, API, and backend tasks.
---

# FastAPI Production & DSA Engineering Guidelines

This skill codifies the architectural rules, DSA constraints, and engineering conventions mandated by the Lead Architect and Mentor. As the Junior Apprentice Backend Engineer, these rules are binding across all tasks.

---

## 1. Architectural Contract: 3-Tier Clean Architecture

Every feature must strictly respect the separation of concerns across layers. Monolithic single-file implementations ("code slop") are strictly prohibited.

```
Request  ──>  [Routers]      (app/routers/)      - Parse requests, validate schemas, HTTP status codes
                 │
              [Services]     (app/services/)     - Business logic, algorithms, domain rules
                 │
             [Repositories] (app/repositories/) - Data access, DB queries, persistence
                 │
              Database / In-Memory Store
```

### Layer Responsibilities
* **Routers (`app/routers/`)**:
  * Handle HTTP request parsing, response status codes, and route definition.
  * Accept and return validated Pydantic schemas (`app/schemas/`).
  * **STRICT RULE**: Zero database queries or heavy business logic in routers. Delegate immediately to the service layer.
* **Services (`app/services/`)**:
  * Contain core business logic, domain rules, and algorithmic processing.
  * Orchestrate calls to one or more repositories.
  * Agnostic to HTTP transport; raise domain exceptions instead of returning `HTTPException` directly where possible.
* **Repositories (`app/repositories/`)**:
  * Manage data access, query execution, and persistence (in-memory or database).
  * Abstract the storage mechanism from the service layer.

---

## 2. Codebase Architecture: Single Evolving System

* **Unified Directory Structure**:
  * All application code must continuously live, evolve, and be refactored inside the unified `app/` directory (`app/routers/`, `app/services/`, `app/repositories/`, `app/schemas/`, `app/core/`, `app/models/`).
  * All automated test suites must reside inside `tests/`.
  * **STRICT FORBIDDEN**: NEVER create fragmented, throwaway, or isolated topic folders such as `day1/`, `day2/`, or `topic_name/`. The codebase grows as a single cohesive enterprise product.

---

## 3. DSA Efficiency Constraints

All algorithms, data transformations, and data structures must be optimized for performance.

* **In-Memory Lookups**:
  * Must be strictly **$\mathcal{O}(1)$** time complexity using Hash Maps (`dict`) or Sets (`set`).
  * **Forbidden**: Linear scans (`for item in list: if item.id == target`) for entity lookups ($\mathcal{O}(n)$).
* **Loops and Iterations**:
  * **Forbidden**: Nested loops yielding $\mathcal{O}(n^2)$ complexity when filtering, transforming, or joining collections.
  * Prefer pre-indexing with dictionaries (`{item.id: item for item in items}`) followed by $\mathcal{O}(1)$ lookups.
* **Space-Time Tradeoffs**:
  * Explicitly justify any time-space tradeoffs when designing in-memory caches, indexes, or memoization tables.

---

## 4. Daily Tracking & Git Commit Contract

* **Roadmap Tracker**:
  * Track 90-day progress inside `ROADMAP.md` at root.
  * Check off days as completed (`[x]`), mark current in-progress as (`[/]`).
* **Daily Learning Logs (`docs/days/`)**:
  * At the end of each day's lesson, generate `docs/days/day-XX.md` with:
    1. Concepts covered today.
    2. Architectural decisions made.
    3. DSA time/space complexity enforced.
    4. Summary of test results.
* **Conventional Commit Format**:
  * At the completion of every day's lesson, propose a standard conventional commit:
    ```bash
    git commit -m "feat(day-XX): brief description of topic"
    ```

---

## 5. Pattern Enforcement

### Good Patterns (Mine - Mandated by Lead Architect)
1. **Single Evolving Codebase**: Keeping all business logic and routes unified in `app/`, continuous refactoring.
2. **Layer Separation**: Clear boundaries across `routers -> services -> repositories`.
3. **Inverted Index Hash Maps**: Maintaining secondary `dict[field, pk]` indexes in repositories to guarantee $\mathcal{O}(1)$ uniqueness validation without linear scans.
4. **Strict Typing**: Full type annotations on all function signatures, parameters, and return types (`mypy --strict` compliant; pytest fixtures with `yield` typed as `Generator[None, None, None]`).
5. **Pydantic Schemas & DTO Separation**: Separate schemas for creation (`UserCreate`), response projection (`UserResponse`), and partial mutations (`UserProfileUpdate`). Internal entity fields (e.g. `password_hash`) must never be declared on response models.
6. **Pydantic v2 Field Constraints**: Enforce length boundaries (`min_length`, `max_length`), regex patterns (`pattern`), numeric ranges (`ge`, `le`), and strict typing (`EmailStr`, `Enum`) directly in `Field()` to leverage C/Rust-speed validation in `pydantic-core`.
7. **Entity Hydration with ConfigDict**: Use `model_config = ConfigDict(from_attributes=True)` on response DTOs for safe serialization from domain models/entities without leaking non-schema fields.
8. **Immutable Defaults & `default_factory`**: Never use mutable objects (`list`, `dict`) as default values in schemas or functions; always use `Field(default_factory=...)` to prevent reference leaks.
9. **Dependency Injection**: Utilize FastAPI's `Depends` for providing repositories and services into router endpoints.
10. **Comprehensive Testing**: Every endpoint and schema must have corresponding unit and integration tests under `tests/`.

### Bad Patterns (Forbidden)
1. **Isolated Day/Topic Folders**: Creating `day1/`, `day2/`, `tutorial/` folders instead of expanding `app/`.
2. **Monolithic Spaghetti**: Putting router logic, DB queries, and schemas inside a single file or endpoint handler.
3. **Manual Validation Inside Routers**: Parsing raw dicts, performing ad-hoc validation loops, or checking lengths/types manually inside router functions instead of relying on Pydantic DTOs.
4. **Exposing Internal Entities**: Returning ORM models, domain entities (`UserEntity`), or raw database records directly to the client instead of mapping to strict response DTOs.
5. **Mutable Default Arguments**: Defining `def fn(items=[])` or `items: list = []` in Pydantic models or function signatures.
6. **Unindexed Searches**: Scanning lists linearly when searching by unique identifier or key.
7. **Direct DB in Routers**: Calling `db.query()`, `session.execute()`, or repository methods directly inside router handlers.
8. **Untyped / `Any` shortcuts**: Using `Any` or omitting return types to bypass type checking.
9. **Swallowing Exceptions**: Bare `except:` clauses without logging and proper error propagation.

---

## 6. Continuous Error Learning & RCA Policy

Whenever a test fails, a bug is caught, or the Lead Architect corrects code:
1. **Never repeat the mistake**: Analyze root cause immediately.
2. **Log to `docs/rca/`**: Create an entry detailing:
   * **Date & Incident**: What went wrong.
   * **Root Cause**: Why the mistake was made.
   * **Resolution**: The corrected code/pattern.
   * **Prevention Rule**: The permanent rule to avoid recurrence.
3. Update this `SKILL.md` with new "Good Patterns" or "Bad Patterns" as instructed.
