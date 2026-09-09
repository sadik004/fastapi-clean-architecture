# RCA: Day 43 - Argon2id Event Loop Starvation & GPU Brute-Force Immunity

- **Date**: 2026-09-09
- **Trigger**: Architectural security analysis and high-concurrency event loop profiling during Day 43 implementation:
  1. The catastrophic "GPU Brute-Force Hazard" of fast cryptographic hash functions (MD5, SHA-256) versus the OWASP Gold Standard memory-hard Argon2id KDF.
  2. The "Asyncio Event Loop Starvation Disaster" caused by executing CPU-intensive Argon2id hashing directly on Python's single-threaded async event loop, blocking concurrent I/O requests and failing Kubernetes liveness probes (`/health`).
  3. The runtime `AttributeError: module 'bcrypt' has no attribute '__about__'` crash when delegating legacy Bcrypt verification to `passlib` under modern `bcrypt` 4.x releases.

- **Faulty Code / Pattern**:
  ```python
  # FLAW 1: Fast hashing algorithm — vulnerable to GPU/ASIC brute-force
  def hash_password_insecure(password: str) -> str:
      return hashlib.sha256(password.encode()).hexdigest()

  # FLAW 2: Synchronous execution on the asyncio event loop — freezes the server
  @router.post("/register")
  async def register_user(req: UserRegisterRequest):
      # Heavy Argon2id calculation takes 50-100ms on the main event loop thread!
      # All concurrent requests (like GET /health) are completely frozen!
      password_hash = hasher.hash(req.password)
      ...

  # FLAW 3: Relying on passlib for bcrypt with bcrypt 4.x installed
  from passlib.hash import bcrypt
  bcrypt.verify(password, hash_str)  # Crashes with AttributeError: 'bcrypt' has no '__about__'
  ```

- **Root Cause**:
  1. **GPU Brute-Force Economics**:
     - Fast hash functions (MD5, SHA-1, SHA-256) were engineered for file checksums and packet integrity, calculating millions of hashes per second.
     - Modern commodity GPUs (e.g., NVIDIA RTX 4090) compute over 20,000,000,000 (20 billion) SHA-256 hashes per second. If a database is breached, attackers can brute-force 8-character complex passwords in seconds.
     - Argon2id is a **Memory-Hard Key Derivation Function (KDF)**. Configured with OWASP recommendations ($m=65,536\text{ KiB} = 64\text{MB}$, $t=3$ iterations, $p=4$ parallelism), computing a single hash requires 64 megabytes of dedicated RAM. A GPU with 24GB of VRAM can fit at most 375 concurrent hashes, rendering GPU dictionary and rainbow table cracking economically and computationally impossible.
  2. **Asyncio Event Loop Starvation**:
     - Python's `asyncio` is single-threaded and relies on cooperative multitasking. When a coroutine awaits I/O (`await db.execute()`), it yields control back to the event loop.
     - Argon2id's memory hardness requires $50\text{--}100\text{ms}$ of 100% CPU computation. Because CPU execution does not yield, running it synchronously halts the event loop entirely.
     - If 10 users register or log in concurrently, the event loop blocks for $1\text{ second}$. During this freeze, incoming TCP connections, websocket packets, and Kubernetes HTTP liveness probes (`GET /health`) time out, triggering false-positive pod restarts and cascading cluster failovers.
  3. **Passlib Dependency Trap with Bcrypt 4.x**:
     - `passlib` has not been updated to support `bcrypt` $\ge 4.0.0$, which removed the private `__about__` attribute. Attempting to verify bcrypt hashes via `passlib.hash.bcrypt` results in an unhandled `AttributeError` at runtime.

- **Resolution**:
  1. **OWASP Argon2id Configuration (`app/core/security.py`)**:
     Engineered `PasswordHasher` strictly following OWASP recommendations:
     ```python
     _argon2_hasher: PasswordHasher = PasswordHasher(
         time_cost=3,
         memory_cost=65536,  # 64 MB
         parallelism=4,
         hash_len=32,
         salt_len=16,
     )
     ```
  2. **Non-Blocking Threadpool Offloading (`asyncio.to_thread`)**:
     Offloaded all CPU-bound hashing and verification routines to Python's background worker threadpool:
     ```python
     async def hash_password_async(password: str) -> str:
         return await asyncio.to_thread(hash_password, password)

     async def verify_password_async(plain_password: str, hashed_password: str) -> bool:
         return await asyncio.to_thread(verify_password_sync, plain_password, hashed_password)
     ```
     The main event loop thread continues processing `/health` and concurrent I/O requests with sub-millisecond response times even while worker threads crunch Argon2id derivations.
  3. **Direct Bcrypt Support via Native C Library**:
     Implemented legacy Bcrypt verification directly using the installed `bcrypt` module (`bcrypt.checkpw()`), completely bypassing `passlib`'s broken wrapper.
  4. **Automatic Transparent Rehash on Login**:
     Integrated `needs_rehash_async` into `UserService.authenticate_user()`. When a user authenticates with an outdated hash (PBKDF2 or Bcrypt), the system verifies the credentials, re-hashes the plaintext password with Argon2id asynchronously, and transparently updates the database within the active transaction boundary.

- **Prevention Rules (Codified into SKILL.md)**:
  1. *Rule 131*: Always use Argon2id memory-hard hashing with OWASP parameters ($m=65536, t=3, p=4$) for password storage.
  2. *Rule 131*: Never execute CPU-bound cryptographic operations directly on the asyncio event loop; always offload to worker threads with `asyncio.to_thread()`.
  3. *Rule 109*: Never use fast hashing algorithms (MD5, SHA-256) for password persistence.
  4. *Rule 110*: Never block the asyncio event loop with synchronous KDF computation.
