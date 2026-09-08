# Day 26: Binary Search (O(log N)) & Two-Pointer Range Filtering Architecture

**Date**: 2026-09-09  
**Role**: Junior Apprentice Backend Engineer  
**Lead Architect & Mentor**: User

---

## 1. Concepts Covered Today
- **Binary Search Range Filtering ($\mathcal{O}(\log N)$ Bisect Bounds)**:
  - Addressed the scalability failure of naive sequential filtering (`[item for item in items if min_val <= item <= max_val]`), which performs an $\mathcal{O}(N)$ linear scan on every incoming request.
  - Implemented `binary_search_bounds()` and `binary_search_range()` in `app/core/dsa/search_algorithms.py`:
    - Locates the lower bound (`left_idx`) via binary search bisect in $\mathcal{O}(\log N)$ time.
    - Locates the upper bound (`right_idx`) starting from `left_idx` in $\mathcal{O}(\log N)$ time.
    - Extracts elements matching the range in $\mathcal{O}(\log N + M)$ time (where $M$ is the number of matched elements), completely bypassing sequential iteration over the non-matching $N - M$ records.
    - Verified under benchmark: over 100,000 sorted elements, boundary lookup executes in $< 0.01\text{ms}$ ($< 10\mu\text{s}$), substantially outperforming the $< 0.2\text{ms}$ performance threshold.
- **Converging Two-Pointer Target Matcher ($\mathcal{O}(N)$ Time, $\mathcal{O}(1)$ Auxiliary Space)**:
  - Eliminated the $\mathcal{O}(N^2)$ quadratic nested loop trap commonly introduced when checking pairs of entities (e.g. finding two items whose metrics sum to a target value).
  - Implemented `two_pointer_pair_search()` in `app/core/dsa/search_algorithms.py`:
    - Places two converging pointers at the opposite ends of a pre-sorted sequence (`left = 0`, `right = N - 1`).
    - Evaluates the current metric sum: advances `left` if sum is too small, decrements `right` if sum is too large.
    - Terminates in at most $N$ iterations ($\mathcal{O}(N)$ time) with strictly zero heap allocations beyond pointer variables ($\mathcal{O}(1)$ space).
- **Service & Transport Layer Integration**:
  - Enhanced `UserService` (`app/services/user_service.py`):
    - Added `filter_users_by_age(min_age, max_age)`: leverages `binary_search_range` over sorted user age projections.
    - Added `find_user_pair_by_age_sum(target_sum)`: leverages `two_pointer_pair_search` to find matching user pairs.
  - Added REST endpoint in `app/routers/user_router.py`:
    - `GET /users/filter/by-age`: accepts query parameters `min_age: int = Query(ge=0, le=150)` and `max_age: int = Query(ge=0, le=150)`.
    - Validates invariant `min_age <= max_age`, returning HTTP 400 Bad Request on inverted bounds.
    - Positioned ahead of `/{user_id}` in routing declaration order to preserve strict literal route precedence and avoid path parameter shadowing.

---

## 2. Key Code Artifacts
- `app/core/dsa/search_algorithms.py`: Pure, generic implementation of `binary_search_bounds`, `binary_search_range`, and `two_pointer_pair_search`.
- `app/core/dsa/__init__.py`: Exported search algorithms into the core DSA package.
- `app/services/user_service.py`: Integrated age range filtering and two-pointer age pairing into domain services.
- `app/routers/user_router.py`: Exposed `GET /users/filter/by-age` with declarative boundary validation.
- `tests/test_binary_search_and_two_pointer.py`: Comprehensive test suite covering boundary edge cases, duplicate elements, 100k element scaling benchmark (< 0.2ms), two-pointer convergence, and endpoint integration.

---

## 3. Verification & Quality Gates
- **Pytest**: 334 tests passed (100% pass rate).
- **Mypy**: `mypy --strict app tests/test_binary_search_and_two_pointer.py` passed with 0 errors across 40 source files.
- **Ruff**: `ruff check app tests alembic` passed cleanly.
- **Benchmark**: 100,000 element binary search executed in $< 0.01\text{ms}$, demonstrating strict $\mathcal{O}(\log N)$ logarithmic time complexity.

---

## 4. Root Cause Analysis (RCA)
- None required. All edge cases, off-by-one boundary invariants, and routing precedence rules were designed and verified without regressions.
