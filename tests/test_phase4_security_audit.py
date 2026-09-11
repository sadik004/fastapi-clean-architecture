"""Comprehensive Architectural Compliance Test Suite for Day 50: Phase 4 Consolidation & Security/Identity Framework Architectural Audit.

Verifies the unified, zero-regression defensive perimeter across all Phase 4 systems:
1. Brute-Force & Rate Limiting Verification (Token Bucket & Sliding Window ZSET).
2. Cache Penetration & Stampede Immunity (Bloom Filter negative gate & XFetch early recomputation).
3. Concurrency & Race Condition Mutex (Optimistic versioning 409 & Pessimistic with_for_update zero overselling).
4. Distributed Mutex & Idempotency (DistributedLock SET NX PX + safe Lua release & IdempotencyManager double-spend shield).
5. Cryptographic Identity & PII Protection (Argon2id, JWT, RTR reuse family revocation, Bitmasking RBAC, ABAC 4-D engine, SSRF CIDR firewall, UUIDv7 B-Tree monotonicity, and Fernet FLE).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import MagicMock

import pytest
from starlette.exceptions import HTTPException

from app.core.abac import (
    EnvironmentContext,
    PolicyEngine,
    PolicyRule,
    ResourceContext,
    SubjectContext,
)
from app.core.config import get_settings
from app.core.dependencies import RateLimitGuard, TokenBucketGuard
from app.core.dsa.bloom_filter import BloomFilter
from app.core.dsa.distributed_lock import DistributedLock
from app.core.dsa.xfetch import XFetchEnvelope, should_recompute
from app.core.encryption import EncryptedString, FernetEngine
from app.core.exceptions import (
    AuthenticationException,
    EncryptionTamperingException,
    InsufficientStockException,
    OptimisticLockException,
    SSRFSecurityException,
)
from app.core.idempotency import IdempotencyManager, compute_request_hash
from app.core.identifiers import extract_timestamp_from_uuidv7, generate_uuidv7
from app.core.permissions import (
    ROLE_ADMIN,
    ROLE_USER,
    Permission,
    grant_permission,
    has_permission,
    revoke_permission,
)
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_jwt_token,
    hash_password_async,
    verify_password_async,
)
from app.core.ssrf_protection import validate_safe_url
from app.models.product import ProductModel
from app.repositories.product_repository import SqlAlchemyProductRepository
from app.repositories.user_repository import InMemoryUserRepository
from app.services.auth_service import AuthService

# ==============================================================================
# SECTION 1: Brute-Force & Rate Limiting Perimeter
# ==============================================================================


@pytest.mark.asyncio
async def test_audit_token_bucket_rate_limiter_perimeter(fake_redis: Any) -> None:
    """Audit 1.1: Verify Token Bucket allows burst capacity and enforces RFC 429 once exhausted."""
    guard = TokenBucketGuard(capacity=3.0, refill_rate=1.0, requested=1.0, scope="audit_tb")

    # Mock HTTP Request with specific client IP
    request = MagicMock()
    request.headers = {}
    request.client.host = "198.51.100.25"
    request.state = MagicMock()

    # Consume 3 tokens successfully (burst allowance)
    for _ in range(3):
        await guard(request=request, redis=fake_redis)

    # 4th immediate request must be throttled with HTTP 429
    with pytest.raises(HTTPException) as exc_info:
        await guard(request=request, redis=fake_redis)

    assert exc_info.value.status_code == 429
    assert exc_info.value.headers is not None
    assert "Retry-After" in exc_info.value.headers
    assert exc_info.value.headers["X-RateLimit-Remaining"] == "0"


@pytest.mark.asyncio
async def test_audit_sliding_window_rate_limiter_perimeter(fake_redis: Any) -> None:
    """Audit 1.2: Verify Distributed Sliding Window ZSET enforces exact window limits."""
    guard = RateLimitGuard(limit=3, window_seconds=60.0, scope="audit_sw")

    request = MagicMock()
    request.headers = {"X-API-Key": "audit-client-secret-key"}
    request.client.host = "192.0.2.1"
    request.state = MagicMock()

    # Fire 3 allowed requests
    for _ in range(3):
        await guard(request=request, redis=fake_redis)

    # 4th request within 60s window must raise HTTP 429
    with pytest.raises(HTTPException) as exc_info:
        await guard(request=request, redis=fake_redis)

    assert exc_info.value.status_code == 429
    assert exc_info.value.headers is not None
    assert exc_info.value.headers["X-RateLimit-Limit"] == "3"
    assert exc_info.value.headers["X-RateLimit-Remaining"] == "0"


# ==============================================================================
# SECTION 2: Cache Penetration & Stampede Immunity
# ==============================================================================


def test_audit_bloom_filter_cache_penetration_prevention() -> None:
    """Audit 2.1: Verify Bloom Filter guarantees zero false negatives and blocks non-existent IDs at Step 0."""
    bloom = BloomFilter(capacity=10_000, false_positive_rate=0.01)

    # Add valid IDs
    for uid in (101, 102, 103, 104, 105):
        bloom.add(uid)

    # Invariant: All added elements MUST be present (Zero False Negatives)
    for uid in (101, 102, 103, 104, 105):
        assert bloom.contains(uid) is True

    # Invariant: Non-existent IDs return False, short-circuiting DB & Cache lookups
    non_existent_ids = [999901, 999902, 999903, 999904, 999905]
    rejected_at_step_zero = [uid for uid in non_existent_ids if not bloom.contains(uid)]
    # With 0.01 FP rate across 5 items, all 5 must be rejected
    assert len(rejected_at_step_zero) == 5


def test_audit_xfetch_probabilistic_cache_stampede_prevention() -> None:
    """Audit 2.2: Verify XFetch probabilistic early recomputation protects expiring hot keys."""
    # Scenario A: Cold item with lots of remaining TTL (should NOT recompute)
    fresh_envelope = XFetchEnvelope(
        val='{"user_id": 42, "name": "Architect"}',
        delta=0.05,  # 50ms compute time
        expiry=datetime.now(UTC).timestamp() + 3600.0,  # 1 hour remaining
    )
    # Using beta=1.0 and fixed random float 0.5
    assert should_recompute(delta=fresh_envelope.delta, expiry=fresh_envelope.expiry, beta=1.0, rand_val=0.5) is False

    # Scenario B: Hot item nearing expiration where stochastic early threshold triggers
    # If delta=2.0s and only 0.1s remaining, early recomputation MUST trigger
    near_expired_envelope = XFetchEnvelope(
        val='{"user_id": 42, "name": "Architect"}',
        delta=2.0,
        expiry=datetime.now(UTC).timestamp() + 0.1,
    )
    # rand_val=0.5 -> -1.0 * 2.0 * ln(0.5) = -2.0 * (-0.693) = +1.386s > 0.1s -> True
    assert (
        should_recompute(
            delta=near_expired_envelope.delta,
            expiry=near_expired_envelope.expiry,
            beta=1.0,
            rand_val=0.5,
        )
        is True
    )


# ==============================================================================
# SECTION 3: Concurrency & Race Condition Mutex
# ==============================================================================


@pytest.mark.asyncio
async def test_audit_optimistic_concurrency_row_versioning() -> None:
    """Audit 3.1: Verify Optimistic Concurrency Control detects stale row version updates and raises 409 Conflict."""
    repo = InMemoryUserRepository()
    user = await repo.create(
        email="occ.user@example.com",
        username="occ_user",
        password_hash="hash",
    )
    assert user.version == 1

    # Update with matching version passes
    updated = await repo.update_with_optimistic_lock(
        user_id=user.id,
        expected_version=1,
        update_data={"email": "occ.user.updated@example.com"},
    )
    assert updated.version == 2

    # Attempt to update with outdated version 1 must trigger OptimisticLockException
    with pytest.raises(OptimisticLockException) as exc_info:
        await repo.update_with_optimistic_lock(
            user_id=user.id,
            expected_version=1,
            update_data={"email": "stale.write@example.com"},
        )
    assert "stale version" in str(exc_info.value).lower() or exc_info.value.code == "OPTIMISTIC_LOCK_CONFLICT"


@pytest.mark.asyncio
async def test_audit_pessimistic_locking_zero_overselling(mock_db_session: Any) -> None:
    """Audit 3.2: Verify Pessimistic Locking with row-level mutex guarantees zero overselling."""
    repo = SqlAlchemyProductRepository(session=mock_db_session)

    # Product with initial stock of 10
    product = ProductModel(
        id=501,
        name="Audited Limited GPU",
        stock=10,
        price=999.99,
    )

    # Mock DB query returning our product with_for_update
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = product
    mock_db_session.execute.return_value = mock_result

    # 1. Deduct 4 items -> stock becomes 6
    updated = await repo.deduct_stock_pessimistic(product_id=501, quantity=4)
    assert updated.stock == 6

    # 2. Deduct remaining 6 items -> stock becomes 0
    updated = await repo.deduct_stock_pessimistic(product_id=501, quantity=6)
    assert updated.stock == 0

    # 3. Attempting to deduct 1 more from 0 stock must raise InsufficientStockException
    with pytest.raises(InsufficientStockException):
        await repo.deduct_stock_pessimistic(product_id=501, quantity=1)


# ==============================================================================
# SECTION 4: Distributed Mutex & Idempotency Perimeter
# ==============================================================================


@pytest.mark.asyncio
async def test_audit_distributed_lock_atomic_mutex_and_release(fake_redis: Any) -> None:
    """Audit 4.1: Verify DistributedLock acquires via SET NX PX and safely releases via token-checked Lua script."""
    lock_a = DistributedLock(redis=fake_redis, name="audit_payout_job", ttl_ms=5000)
    lock_b = DistributedLock(redis=fake_redis, name="audit_payout_job", ttl_ms=5000)

    # Lock A acquires mutex
    assert await lock_a.acquire() is True
    assert lock_a.is_acquired is True

    # Lock B must fail to acquire while A holds the resource
    assert await lock_b.acquire() is False
    assert lock_b.is_acquired is False

    # A different fake lock cannot release Lock A's key (safe Lua token check)
    tamper_lock = DistributedLock(redis=fake_redis, name="audit_payout_job", ttl_ms=5000)
    tamper_lock.token = "malicious_spoofed_token"
    tamper_lock._acquired = True
    assert await tamper_lock.release() is False

    # Lock A releases legitimately
    assert await lock_a.release() is True

    # Now Lock B can acquire cleanly
    assert await lock_b.acquire() is True
    await lock_b.release()


@pytest.mark.asyncio
async def test_audit_idempotency_manager_double_spend_shield(fake_redis: Any) -> None:
    """Audit 4.2: Verify IdempotencyManager enforces IN_PROGRESS locks, payload verification, and cached replays."""
    manager = IdempotencyManager(redis=fake_redis)
    key = "idem-tx-audit-9988"
    req_hash = compute_request_hash("POST", "/payments/charge", b'{"amount": 500.0, "currency": "USD"}')

    # 1. First execution acquires lock
    is_cached, record = await manager.check_or_acquire(key=key, request_hash=req_hash, ttl_seconds=3600)
    assert is_cached is False
    assert record is None

    # 2. Concurrent duplicate request while in progress raises conflict 409
    with pytest.raises(HTTPException) as exc_info:
        await manager.check_or_acquire(key=key, request_hash=req_hash, ttl_seconds=3600)
    assert exc_info.value.status_code == 409

    # 3. Complete transaction and record response
    await manager.record_success(
        key=key,
        request_hash=req_hash,
        status_code=201,
        response_body={"transaction_id": "tx_9988", "status": "settled"},
    )

    # 4. Subsequent retry with identical payload returns cached result
    is_cached_replay, replay_record = await manager.check_or_acquire(key=key, request_hash=req_hash, ttl_seconds=3600)
    assert is_cached_replay is True
    assert replay_record is not None
    assert replay_record["status_code"] == 201
    assert replay_record["response_body"]["transaction_id"] == "tx_9988"

    # 5. Reusing the same key with altered payload raises tampering exception 422
    tampered_hash = compute_request_hash("POST", "/payments/charge", b'{"amount": 99999.0, "currency": "USD"}')
    with pytest.raises(HTTPException) as exc_info_tamper:
        await manager.check_or_acquire(key=key, request_hash=tampered_hash, ttl_seconds=3600)
    assert exc_info_tamper.value.status_code == 422


# ==============================================================================
# SECTION 5: Cryptographic Identity & PII Protection
# ==============================================================================


@pytest.mark.asyncio
async def test_audit_argon2id_cryptographic_password_hashing() -> None:
    """Audit 5.1: Verify Argon2id produces OWASP-compliant memory-hard hashes and verifies accurately."""
    password = "SuperSecretAuditedPassword2026!"
    hashed = await hash_password_async(password)

    # Must adhere to Argon2id RFC 9106 format with memory parameters
    assert hashed.startswith("$argon2id$v=19$m=65536,t=3,p=4$")
    assert await verify_password_async(password, hashed) is True
    assert await verify_password_async("WrongPassword123!", hashed) is False


@pytest.mark.asyncio
async def test_audit_jwt_stateless_and_rtr_family_revocation(fake_redis: Any) -> None:
    """Audit 5.2: Verify JWT claims verification and Redis-backed Refresh Token Rotation reuse revocation."""
    settings = get_settings()
    user_id = 99

    # 1. Stateless Access Token
    access_token = create_access_token(user_id=user_id, role="admin")
    payload = decode_jwt_token(access_token, expected_type="access")
    assert int(payload["sub"]) == user_id
    assert payload["role"] == "admin"

    # 2. Refresh Token Rotation Engine
    auth_service = AuthService(user_service=MagicMock(), redis=fake_redis, settings=settings)
    family_id = "family_audit_101"

    token_1, jti_1 = create_refresh_token(user_id=user_id, family_id=family_id)
    family_key = f"rtr:family:{family_id}"
    # Seed initial family state
    import json

    await fake_redis.set(
        family_key,
        json.dumps({"active_jti": jti_1, "user_id": user_id, "role": "admin"}),
        ex=86400,
    )

    # Rotate token legitimately: token_1 -> token_2
    rotated_res = await auth_service.refresh_tokens(token_1)
    assert rotated_res.refresh_token is not None
    token_2 = rotated_res.refresh_token

    # State in Redis now has token_2's jti as active_jti
    active_record = json.loads(await fake_redis.get(family_key))
    assert active_record["active_jti"] != jti_1

    # Attacker tries to reuse burned token_1 -> Triggers theft detection and family revocation
    with pytest.raises((HTTPException, AuthenticationException)) as exc_info:
        await auth_service.refresh_tokens(token_1)
    if isinstance(exc_info.value, HTTPException):
        assert exc_info.value.status_code == 401
        assert "reuse detected" in exc_info.value.detail.lower()
    elif isinstance(exc_info.value, AuthenticationException):
        assert "reuse detected" in exc_info.value.message.lower()

    # Now the entire family has been revoked (deleted from Redis)
    assert await fake_redis.get(family_key) is None

    # Now even the legitimate token_2 is rejected because the family was revoked!
    with pytest.raises((HTTPException, AuthenticationException)) as exc_info_2:
        await auth_service.refresh_tokens(token_2)
    if isinstance(exc_info_2.value, HTTPException):
        assert exc_info_2.value.status_code == 401
    elif isinstance(exc_info_2.value, AuthenticationException):
        assert "expired or revoked" in exc_info_2.value.message.lower()


def test_audit_bitmasking_rbac_o1_bitwise_permissions() -> None:
    """Audit 5.3: Verify Bitmasking RBAC provides O(1) mathematical permission validation."""
    # User with READ + WRITE
    user_perms = ROLE_USER
    assert has_permission(user_perms, Permission.READ) is True
    assert has_permission(user_perms, Permission.WRITE) is True
    assert has_permission(user_perms, Permission.DELETE) is False

    # Grant DELETE to user
    elevated_perms = grant_permission(user_perms, Permission.DELETE)
    assert has_permission(elevated_perms, Permission.DELETE) is True

    # Revoke WRITE
    revoked_perms = revoke_permission(elevated_perms, Permission.WRITE)
    assert has_permission(revoked_perms, Permission.WRITE) is False

    # Admin has all permissions
    admin_perms = ROLE_ADMIN
    assert has_permission(admin_perms, Permission.ADMIN) is True
    assert has_permission(admin_perms, Permission.DELETE) is True


def test_audit_abac_dynamic_policy_engine_default_deny() -> None:
    """Audit 5.4: Verify ABAC 4-dimensional policy engine enforces strict default-deny and tenant isolation."""
    engine = PolicyEngine()

    # Rule: Users can only read resources belonging to their own tenant
    tenant_isolation_rule = PolicyRule(
        name="tenant_isolation_guard",
        resource_type="document",
        action="read",
        predicate=lambda s, r, a, e: s.tenant_id == r.tenant_id,
    )
    engine.register_rule(tenant_isolation_rule)

    subject_a = SubjectContext(user_id=1, role="user", tenant_id="tenant_alpha")
    subject_b = SubjectContext(user_id=2, role="user", tenant_id="tenant_beta")
    resource_alpha = ResourceContext(
        resource_type="document",
        resource_id=101,
        owner_id=1,
        tenant_id="tenant_alpha",
    )
    env = EnvironmentContext(
        current_time=datetime.now(UTC),
        client_ip="127.0.0.1",
        is_business_hours=True,
    )

    # Same tenant -> Allowed
    assert engine.evaluate(subject_a, resource_alpha, "read", env) is True
    # Cross tenant -> Denied
    assert engine.evaluate(subject_b, resource_alpha, "read", env) is False
    # Unregistered action -> Default Deny
    assert engine.evaluate(subject_a, resource_alpha, "delete", env) is False


def test_audit_ssrf_ip_firewall_blocks_forbidden_ranges() -> None:
    """Audit 5.5: Verify SSRF protection strictly blocks loopback, private networks, and cloud metadata IMDS."""
    # Cloud metadata (AWS/GCP/Azure IMDS)
    with pytest.raises(SSRFSecurityException):
        validate_safe_url("http://169.254.169.254/latest/meta-data/")

    # Loopback
    with pytest.raises(SSRFSecurityException):
        validate_safe_url("http://127.0.0.1:8000/internal")

    # Private RFC 1918 networks
    with pytest.raises(SSRFSecurityException):
        validate_safe_url("http://10.0.0.1/admin")
    with pytest.raises(SSRFSecurityException):
        validate_safe_url("http://192.168.1.1/router")

    # Non-HTTP/HTTPS schemes
    with pytest.raises(SSRFSecurityException):
        validate_safe_url("file:///etc/passwd")


def test_audit_uuidv7_time_ordered_monotonicity() -> None:
    """Audit 5.6: Verify UUIDv7 guarantees B-Tree index monotonicity and O(1) timestamp extraction."""
    id_1 = generate_uuidv7()
    id_2 = generate_uuidv7()

    # Monotonic B-Tree property: id_2 must be strictly greater than or equal to id_1
    assert id_2 >= id_1

    # Extract UTC timestamp without database query
    ts_extracted = extract_timestamp_from_uuidv7(id_1)
    now_utc = datetime.now(UTC)
    delta_seconds = abs((now_utc - ts_extracted).total_seconds())
    # Extraction must be within 2 seconds of current clock
    assert delta_seconds < 2.0


def test_audit_fernet_field_level_encryption_at_rest() -> None:
    """Audit 5.7: Verify Fernet FLE transparently encrypts at rest and detects ciphertext tampering."""
    engine = FernetEngine()
    sensitive_nid = "19920101999988"

    # Encrypt
    token = engine.encrypt(sensitive_nid)
    assert token.startswith("gAAAAA")
    assert sensitive_nid not in token

    # Decrypt
    decrypted = engine.decrypt(token)
    assert decrypted == sensitive_nid

    # Tampering Detection: Alter one character of ciphertext
    corrupted_token = token[:-5] + ("A" if token[-5] != "A" else "B") + token[-4:]
    with pytest.raises(EncryptionTamperingException):
        engine.decrypt(corrupted_token)

    # SQLAlchemy TypeDecorator integration
    decorator = EncryptedString()
    assert decorator.process_bind_param(None, None) is None
    assert decorator.process_result_value(None, None) is None
    bound_ciphertext = decorator.process_bind_param(sensitive_nid, None)
    assert bound_ciphertext is not None
    assert bound_ciphertext.startswith("gAAAAA")
    restored_plaintext = decorator.process_result_value(bound_ciphertext, None)
    assert restored_plaintext == sensitive_nid
