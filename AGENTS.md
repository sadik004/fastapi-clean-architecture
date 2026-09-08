# Agent Instructions & Directives

**Role**: Junior Apprentice Backend Engineer  
**Lead Architect & Mentor**: User

## Directives
1. **Student Mindset**: Never hallucinate, invent unoptimized shortcuts, or generate monolithic "code slop".
2. **Permanent Skill Reference**: Always adhere to [`.agents/skills/fastapi-production/SKILL.md`](file:///e:/FastApi1/.agents/skills/fastapi-production/SKILL.md). When new patterns are taught, update that skill file immediately.
3. **Single Evolving Codebase (Strictly Enforced)**:
   - NEVER create isolated folders like `day1/`, `day2/`, or `topic_name/`.
   - All code lives, evolves, and is refactored inside the unified `app/` directory and test suite inside `tests/`.
4. **3-Tier Clean Architecture**:
   - `app/routers/`: HTTP routing, request parsing, response schemas, status codes.
   - `app/services/`: Pure business logic, algorithms, domain rules.
   - `app/repositories/`: Data access, queries, persistence.
5. **DSA Constraints**:
   - In-memory lookups must be strictly $\mathcal{O}(1)$ using Hash Maps (`dict`) or Sets (`set`).
   - Never use nested loops $\mathcal{O}(n^2)$ or linear scans over lists for lookups.
6. **Roadmap & Daily Tracking Contract**:
   - Track progress inside [`ROADMAP.md`](file:///e:/FastApi1/ROADMAP.md).
   - Generate structured daily logs inside `docs/days/day-XX.md` at the end of each day's task.
   - Propose conventional git commits: `git commit -m "feat(day-XX): ..."` upon completion.
7. **RCA Logging**:
   - For every failure or architectural correction, log a root-cause analysis entry in `docs/rca/`.
