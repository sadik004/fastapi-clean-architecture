# FastAPI Clean Architecture & Production Engineering

A production-grade, 90-day enterprise FastAPI backend architecture demonstrating clean architecture, asynchronous database persistence, high-performance data structures, and rigorous automated testing.

---

## 🏛️ Architecture Overview

The codebase is strictly structured according to **3-Tier Clean Architecture** and domain-driven design principles:

```text
FastApi1/
├── app/
│   ├── api/
│   │   └── v1/
│   │       ├── endpoints/        # HTTP presentation & route controllers
│   │       └── router.py         # Versioned API router aggregation
│   ├── core/
│   │   ├── config.py             # Immutable settings via pydantic-settings
│   │   ├── database.py           # SQLAlchemy 2.0 async engine & sessionmaker
│   │   ├── dependencies.py       # Declarative dependency injection graph
│   │   ├── dsa/                  # Custom algorithms, data structures & memory profilers
│   │   ├── exceptions.py         # Decoupled domain exception hierarchy
│   │   └── unit_of_work.py       # Unit of Work protocol & implementations
│   ├── models/                   # SQLAlchemy 2.0 declarative ORM entities
│   ├── repositories/             # Data persistence contracts & concrete implementations
│   ├── schemas/                  # Strict Pydantic v2 validation DTOs
│   ├── services/                 # Pure domain business logic & transaction orchestration
│   └── main.py                   # Lifespan management, middleware & exception handlers
├── alembic/                      # Version-controlled async database migrations
├── docs/
│   ├── days/                     # Structured daily engineering logs (Days 1–22)
│   └── rca/                      # Root Cause Analysis (RCA) incident records
└── tests/                        # Comprehensive test suite (298+ tests)
```

---

## 🚀 Progress & Curriculum Roadmap

Tracked in detail within [`ROADMAP.md`](ROADMAP.md).

### Phase 1: Foundation, Validation & Core Architecture (Days 1–15) — Complete `[15/15]`
- **Day 01–06**: 3-Tier Clean Architecture, Pydantic v2 validation, custom inverted indexes, Regex precompilation, and DTO isolation.
- **Day 07–10**: Pytest test architecture, route precedence, timing-attack-safe auth (`secrets.compare_digest`), IDOR guards, and generator fixture teardowns.
- **Day 11–13**: Asynchronous concurrency (`asyncio.gather`, `asyncio.to_thread`), event loop health isolation, and memory-bounded background tasks.
- **Day 14–15**: Centralized enterprise error envelopes (`ErrorResponse`), safe 500 traceback masking, and modern SQLAlchemy 2.0 async engine setup.

### Phase 2: Async Engine, Advanced Validation & Testing Rigor (Days 16–30) — In Progress `[7/15]`
- **Day 16**: Connection pooling (`pool_size`, `max_overflow`, `pool_pre_ping=True`).
- **Day 17**: Version-controlled migrations with Async Alembic & declarative models.
- **Day 18**: The Repository Pattern (`SqlAlchemyUserRepository` & domain entity decoupling).
- **Day 19**: N+1 query prevention via `selectinload` / `joinedload` with defensive `lazy="raise"`.
- **Day 20**: The Unit of Work (UoW) Pattern for atomic multi-repository ACID transactions.
- **Day 21**: CPython dictionary internals (compact hash table, perturbation probing, tombstones).
- **Day 22**: Deep memory optimization with `__slots__` and `@dataclass(slots=True)` (> 40–62% RAM reduction).

---

## 🧪 Quality Gates & Verification

All commits must satisfy 100% compliance with quality gates:

```bash
# 1. Run complete automated test suite (298 passed)
pytest tests -v

# 2. Strict type checking across all modules
mypy --strict app tests alembic

# 3. Code formatting & linting
ruff check app tests alembic
```

---

## 📚 Documentation & RCA Knowledge Base

- **Curriculum Roadmap**: [`ROADMAP.md`](ROADMAP.md)
- **Daily Engineering Logs**: [`docs/days/`](docs/days/)
- **Root Cause Analysis (RCA) Catalog**: [`docs/rca/README.md`](docs/rca/README.md)
- **Production Engineering Directive**: [`.agents/skills/fastapi-production/SKILL.md`](.agents/skills/fastapi-production/SKILL.md)
