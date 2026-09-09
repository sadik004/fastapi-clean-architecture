# Day 44: Stateless Authentication Architecture with JWT Lifecycle (HS256 vs RS256) & Redis-Backed Refresh Token Rotation (RTR)

## 1. Overview & Architectural Objectives
Traditional stateful session authentication stores session tokens in database tables (e.g. `sessions`), forcing every incoming HTTP request to execute `SELECT * FROM sessions WHERE token = ?`. In high-throughput distributed microservices, this saturates relational database connection pools and introduces severe network latency.

Day 44 implements an enterprise-grade **Stateless Authentication Architecture** using **Json Web Tokens (JWT)** and **Redis-Backed Refresh Token Rotation (RTR)** with active **Theft / Replay Detection**:

1. **Stateless JWT Verification ($\mathcal{O}(1)$ Local Signature Math)**:
   - Access tokens are cryptographically signed with claims: `sub` (user ID), `role`, `token_type` (`"access"`), `jti` (unique UUID), `iat`, and `exp`.
   - The `get_current_authenticated_user` dependency verifies signatures in memory in $\mathcal{O}(1)$ time without querying the database, reducing database read pressure to zero for authenticated routes.
   - Dual-algorithm support:
     - **Symmetric HS256**: Fast HMAC-SHA256 signing using `Settings.jwt_secret_key` for monolithic or trusted internal clusters.
     - **Asymmetric RS256**: RSA signing with a private key (`Settings.jwt_private_key`) and verification with a public key (`Settings.jwt_public_key`), allowing edge microservices to verify identities without holding signing authority.
2. **Short-Lived Access Tokens (15 Minutes)**:
   - Limits the damage window if an access token is leaked via client-side XSS or network eavesdropping to at most 15 minutes.
3. **Redis-Backed Refresh Token Rotation (RTR)**:
   - Long-lived refresh tokens (default 7 days) are tied to a **Token Family** (`family_id`) and assigned a unique single-use `jti`.
   - On rotation (`POST /auth/refresh`), the old refresh token is consumed/burned and a brand new token pair (new access token + new refresh token with new `jti`) is issued.
4. **Theft & Replay Detection Invariant**:
   - If an attacker replays an already consumed/burned refresh token (`incoming_jti != stored.active_jti`), the engine detects the breach immediately.
   - **Immediate Action**: Revokes the ENTIRE token family in Redis (`DEL rtr:family:{family_id}`), instantly invalidating all active sessions for both the attacker and the victim, rejecting the request with `HTTP 401 Unauthorized`.

---

## 2. Token Lifecycle & Theft Detection State Machine

```
Client Login (POST /auth/login)
          │
          ▼
   AuthService.login()
          │
          ├──► Generate family_id
          ├──► Generate access_token (15m)
          ├──► Generate refresh_token_1 (7d, jti_1)
          └──► Redis SET rtr:family:{family_id} -> { active_jti: jti_1 }
                   │
                   ▼
Client Refresh (POST /auth/refresh with refresh_token_1)
          │
          ▼
   AuthService.refresh_tokens()
          │
          ├── Check incoming_jti == stored.active_jti?
          │
          ├──► YES (Legitimate Rotation):
          │         ├── Generate refresh_token_2 (new jti_2)
          │         ├── Generate access_token_2
          │         ├── Redis UPDATE: active_jti = jti_2 (Preserving remaining TTL)
          │         └── Return new token pair (token_1 is now BURNED!)
          │
          └──► NO (Theft / Replay Detected!):
                    ├── Burned token presented!
                    ├── Redis DELETE rtr:family:{family_id} (Revoke whole family!)
                    └── Raise HTTP 401: "Refresh token reuse detected. All sessions revoked for security."
```

---

## 3. Core Components Implemented

### 3.1 JWT Cryptographic Engine (`app/core/security.py`)
- `create_access_token(user_id, role, expires_delta, algorithm, key)`: Signs 15-minute access token.
- `create_refresh_token(user_id, family_id, expires_delta, algorithm, key)`: Signs 7-day refresh token, returning `(token, jti)`.
- `decode_jwt_token(token, expected_type, algorithm, key)`: Verifies signature, expiration, and token type.

### 3.2 Authentication Service (`app/services/auth_service.py`)
- `AuthService`:
  - `login(payload: LoginRequest)`: Verifies Argon2id credentials via `UserService.authenticate_user()`, generates token pair, and initializes Redis family.
  - `refresh_tokens(refresh_token: str)`: Executes RTR, checks `active_jti`, burns old token, or revokes entire family on replay.
  - `logout(refresh_token: str)`: Deletes `rtr:family:{family_id}` from Redis.

### 3.3 Transport & API Boundaries
- `app/schemas/auth.py`: `LoginRequest`, `TokenResponse`, `RefreshTokenRequest`, `AuthenticatedUserResponse`, `LogoutResponse`.
- `app/core/dependencies.py`:
  - `get_current_authenticated_user`: Decodes Bearer token in $\mathcal{O}(1)$ time without database queries.
  - `get_auth_service`: Dependency provider yielding configured `AuthService`.
- `app/routers/auth_router.py`:
  - `POST /auth/login`
  - `POST /auth/refresh`
  - `POST /auth/logout`
  - `GET /auth/me`
- Mounted `auth_router` in `app/main.py`.

---

## 4. Verification & Testing

### 4.1 Test Suite (`tests/test_jwt_and_refresh_token_rotation.py`)
- `test_login_and_token_generation`: Verifies 15m access token and 7d refresh token issuance.
- `test_login_invalid_credentials_rejected`: Verifies bad password or missing user returns HTTP 401.
- `test_stateless_endpoint_verification_no_db`: Confirms `/auth/me` resolves claims with zero DB queries.
- `test_expired_access_token_rejection`: Asserts expired token returns HTTP 401 with `WWW-Authenticate: Bearer`.
- `test_invalid_token_type_or_signature_rejection`: Rejects refresh tokens passed to access routes or corrupted tokens.
- `test_refresh_token_rotation_lifecycle_and_theft_detection`: Verifies legitimate rotation burns old token, and replay triggers theft detection and family deletion.
- `test_logout_revokes_token_family`: Confirms logout deletes Redis family and prevents subsequent refresh.
- `test_asymmetric_rs256_cryptographic_lifecycle`: Generates RSA key pair, signs with private key, and verifies with public key.

### 4.2 Quality Gates
- `pytest tests/test_jwt_and_refresh_token_rotation.py`: 8 passed in 1.38s.
- `pytest tests -q`: 445 passed, 2 warnings in 44.36s (100% pass rate).
- `ruff check app tests alembic`: All checks passed.
- `mypy --strict app tests alembic`: Success: 0 issues in 113 source files.
