# RCA: Day 38 - Redis Lua Script Python Comment Syntax Injection & Lupa Runtime Dependency

- **Date**: 2026-09-09
- **Trigger**: Pytest ResponseError in `test_token_bucket_rate_limiter.py`:
  `redis.exceptions.ResponseError: Error running script: error loading code: [string "<python>"]:1: unexpected symbol near '#'`
  and initial `ModuleNotFoundError: No module named 'lupa'` in `fakeredis`.
- **Faulty Code / Pattern**:
  ```python
  # In RateLimiterService:
  BUCKET_LUA_SCRIPT: str = """  # noqa: S105
  local key = KEYS[1]
  ...
  """
  ```
- **Root Cause**:
  1. In Python, placing a comment on the opening line of a multi-line docstring or triple-quoted string (`""" # noqa: S105`) includes the comment text (` # noqa: S105\n`) inside the string value itself.
  2. Unlike Python or Bash, the Lua programming language uses `--` for comments. In Lua, `#` is the unary length operator and cannot appear at the start of a statement. When `redis.script_load()` passed the string to Lua bytecode compiler, it encountered `#` as the very first character and failed with `unexpected symbol near '#'`.
  3. Additionally, `fakeredis` requires the underlying C-extension `lupa` (Lua binding for Python) to simulate Redis Lua script execution (`SCRIPT LOAD`, `EVAL`, `EVALSHA`). Without `lupa`, `FakeRedis` raises `ResponseError: unknown command 'script'`.
- **Resolution**:
  1. Configured Bandit `S105` per-file ignore in `pyproject.toml` for `app/services/rate_limiter_service.py`, allowing rate-limiting token variables without inline `# noqa` comments inside the Lua string definition:
     ```toml
     [tool.ruff.lint.per-file-ignores]
     "app/services/rate_limiter_service.py" = [
         "S105", # Rate limiter token bucket naming is not a hardcoded password
     ]
     ```
  2. Preserved the Lua script string as 100% pure Lua code starting directly with Lua statements and `--` comments.
  3. Installed `lupa` in the environment so `fakeredis` can accurately execute Redis Lua scripts during automated testing.
- **Permanent Prevention Rule**:
  1. Never place Python inline comments (`# noqa`) on the same line as the opening triple-quotes of embedded multi-line scripts (SQL, Lua, Shell). Always use configuration-level linter ignores (`per-file-ignores`) or place the comment on a separate non-string line.
  2. For projects testing Redis Lua scripts with `fakeredis`, always declare and install `lupa` (`fakeredis[lua]` or `lupa>=2.0`) to avoid runtime command rejection.
