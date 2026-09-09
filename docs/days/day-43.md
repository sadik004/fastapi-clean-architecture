# Day 43: Cryptographic Password Security with Argon2id & Asyncio Event Loop Offloading

## 1. Overview & Architectural Objectives
In production web services, user authentication is the primary defense boundary. Traditional fast cryptographic hashing algorithms (MD5, SHA-1, SHA-256) were designed for message integrity, calculating hashes in microseconds. Modern commodity GPUs and ASICs execute tens of billions of SHA-256 hashes per second, allowing attacker dictionary attacks to crack passwords in minutes. Furthermore, running synchronous, CPU-intensive password hashing directly inside Python's `asyncio` event loop blocks all concurrent I/O, causing request starvation, health check failures, and cluster failover disasters.

Day 43 re-engineers password security using the **OWASP Gold Standard: Argon2id Memory-Hard Key Derivation Function (KDF)** with non-blocking event loop offloading and transparent legacy password migration:

1. **Argon2id Cryptographic Superiority (OWASP Gold Standard)**:
   - Winner of the Password Hashing Competition (PHC), Argon2id combines Argon2d (resistance against GPU cracking) and Argon2i (resistance against side-channel timing attacks).
   - Configured with OWASP-recommended parameters:
     - Memory Cost ($m$): $65,536\text{ KiB}$ ($64\text{ MB}$)
     - Time Cost ($t$): $3$ iterations
     - Parallelism ($p$): $4$ concurrent threads / lanes
     - Hash Length: $32$ bytes
     - Salt Length: $16$ bytes
   - The memory hardness makes GPU and ASIC hardware scaling economically unviable for brute-force attackers.
2. **Non-Blocking Asyncio Event Loop Offloading**:
   - Argon2id computations take $\sim 50\text{–}100\text{ms}$ of 100% CPU. Direct execution blocks Python's single-threaded event loop.
   - All hashing (`hash_password_async`) and verification (`verify_password_async`) routines offload CPU-bound calculations to Python's internal worker threadpool via `asyncio.to_thread()`, keeping the event loop unblocked.
   - Concurrent requests (e.g., `/health` or cache fetches) respond in sub-milliseconds even under heavy registration and login workloads.
3. **Multi-Algorithm Verification & Backward Compatibility**:
   - `verify_password_sync` detects hash formats dynamically:
     - Argon2id: `$argon2id$v=19$...`
     - Legacy PBKDF2: `pbkdf2:sha256:...`
     - Legacy Bcrypt: `$2b$...`, `$2a$...`, `$2y$...` (using direct `bcrypt.checkpw`, avoiding `passlib` version incompatibilities)
4. **Transparent Rehash on Authentication**:
   - `needs_rehash(hash_str)` flags any password hash not matching current OWASP Argon2id parameters.
   - When a user with a legacy PBKDF2 or Bcrypt hash logs in with correct credentials, `UserService.authenticate_user()` automatically and transparently upgrades the stored hash to Argon2id in the background within the active transaction.

---

## 2. Event Loop Offloading & Transparent Rehash Lifecycle

```
Client Registration / Login Request
             │
             ▼
      FastAPI Route
             │
             ▼
   UserService.authenticate_user()
             │
             ├──► verify_password_async(plain, hash_str)
             │          │
             │          ▼
             │    asyncio.to_thread(verify_password_sync) ────► [Worker Threadpool]
             │          │                                         │ (CPU-bound Argon2id verification)
             │          │                                         │
             │          ◄─────────────────────────────────────────┘
             │
             ├──► Verification Failed? ──► Raise AuthenticationException (HTTP 401)
             │
             └──► Verification Succeeded
                        │
                        ├──► needs_rehash_async(hash_str)
                        │          │
                        │          ├── Legacy Hash (PBKDF2/Bcrypt) detected?
                        │          │         │
                        │          │         ▼
                        │          │    hash_password_async(plain)
                        │          │         │
                        │          │         ▼
                        │          │    user_repo.update(user.id, password_hash=new_argon2id)
                        │          │         │
                        │          │         ▼
                        │          │    uow.commit() [Transparent Database Upgrade]
                        │          │
                        │          └── Already Argon2id? ──► No update needed
                        │
                        ▼
             Return Authenticated User & Issue JWT Token
```

---

## 3. Core Components Implemented

### 3.1 Cryptographic Engine (`app/core/security.py`)
- `_argon2_hasher`: Initialized with `time_cost=3`, `memory_cost=65536`, `parallelism=4`, `hash_len=32`, `salt_len=16`.
- `hash_password(plain)` & `hash_password_async(plain)`: Generates OWASP-compliant `$argon2id$` hashes.
- `verify_password_sync(plain, hash_str)` & `verify_password_async(plain, hash_str)`: Multi-algorithm dispatch for Argon2id, PBKDF2, and Bcrypt.
- `needs_rehash(hash_str)` & `needs_rehash_async(hash_str)`: Detects legacy formats or parameter changes.
- Backward compatibility: `get_password_hash` and `verify_password` exported as aliases.

### 3.2 Repository Layer Updates
- `app/repositories/user_repository.py`:
  - `UserRepositoryProtocol.update(user_id, email, username, is_active, password_hash)`
  - `InMemoryUserRepository.update()`: Added update for `password_hash`.
- `app/repositories/sqlalchemy_user_repository.py`:
  - `SqlAlchemyUserRepository.update()`: Added dynamic `values["password_hash"]` update.

### 3.3 Domain Service Integration (`app/services/user_service.py`)
- Updated `register_user` and `create_user_with_initial_post` to use `await hash_password_async()`.
- Updated `authenticate_user` to use `await verify_password_async()`.
- Added transparent rehash trigger on login when `await needs_rehash_async()` returns `True`.

---

## 4. Verification & Testing

### 4.1 Test Suite (`tests/test_password_security_argon2.py`)
- **`test_argon2id_hashing_and_verification`**: Confirms hash format `$argon2id$v=19$m=65536,t=3,p=4$`, non-deterministic unique salts, and correct password matching.
- **`test_event_loop_not_blocked_during_heavy_hashing`**: Concurrently computes 4 parallel Argon2id hashes while firing rapid `/health` API requests, asserting event loop responsiveness and sub-50ms latency.
- **`test_needs_rehash_detection`**: Verifies `needs_rehash` flags PBKDF2, Bcrypt, and non-conforming parameters, while returning `False` for valid Argon2id hashes.
- **`test_transparent_rehash_on_login`**: Populates database with a legacy PBKDF2 user, authenticates with valid credentials, and asserts the stored hash in the database is upgraded to Argon2id without user intervention.
- **`test_invalid_password_returns_false`**: Verifies mismatch handling returns `False` and invalid formats raise no unexpected exceptions.

### 4.2 Regression & Quality Gates
- `pytest tests/test_password_security_argon2.py`: 5 passed in 0.81s.
- `pytest tests/test_blocking_vs_nonblocking.py`: 5 passed in 2.78s.
- `pytest tests -q`: 437 passed, 2 warnings in 42.89s.
- `ruff check app tests alembic`: All checks passed.
- `mypy --strict app tests alembic`: Success: no issues found in 109 source files.
