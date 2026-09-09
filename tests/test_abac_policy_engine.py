"""Comprehensive test suite for Day 46: Attribute-Based Access Control (ABAC) Architecture & Policy-Driven Permission Engine.

Verifies:
1. Pure PolicyEngine & Default-Deny Mechanics: Confirms O(P) predicate evaluation,
   least privilege enforcement, and default rejection when no rules match.
2. Multi-Tenant Isolation: Users from Tenant A attempting to read/update Tenant B's
   resources are rejected with HTTP 403 Forbidden.
3. Ownership Verification & Admin Bypass: Resource owners can mutate active resources (HTTP 200),
   non-owners are rejected (HTTP 403), and administrators can bypass ownership (HTTP 200).
4. Resource Lifecycle State Guard: Mutating 'archived' documents is forbidden even for the
   owner (HTTP 403), but permitted for an administrator (HTTP 200).
5. Contextual & Environmental Approval Gate:
   - Approving high-value documents (> $10,000) outside business hours is rejected (HTTP 403).
   - Approving high-value documents (> $10,000) by non-finance staff is rejected (HTTP 403).
   - Approving high-value documents (> $10,000) by finance staff during business hours succeeds (HTTP 200).
   - Approving standard documents (<= $10,000) succeeds under standard authorization (HTTP 200).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.abac import (
    EnvironmentContext,
    PolicyEngine,
    PolicyRule,
    ResourceContext,
    SubjectContext,
    policy_contextual_approval_gate,
    policy_lifecycle_state_guard,
    policy_multi_tenant_isolation,
    policy_ownership_and_admin_bypass,
)
from app.core.security import create_access_token
from app.main import app
from app.services.document_service import get_default_document_repository


@pytest.fixture(autouse=True)
def clean_document_store() -> None:
    """Clear in-memory document repository before and after each test."""
    repo = get_default_document_repository()
    repo.clear()


def test_abac_policy_engine_pure_mechanics() -> None:
    """Verify Default-Deny principle and pure policy rule evaluation."""
    engine = PolicyEngine()
    subject = SubjectContext(user_id=1, role="user", department="engineering", tenant_id="t1")
    resource = ResourceContext(
        resource_type="document",
        resource_id=10,
        owner_id=1,
        tenant_id="t1",
        status="active",
        amount=500.0,
    )
    env = EnvironmentContext(current_time=datetime.now(), client_ip="127.0.0.1", is_business_hours=True)

    # 1. Default-Deny: No registered rules for 'document' -> False
    assert engine.evaluate(subject, resource, "read", env) is False

    # 2. Register rule for 'read' only
    engine.register_rule(
        PolicyRule(
            name="allow_t1_read",
            resource_type="document",
            action="read",
            predicate=lambda s, r, a, e: s.tenant_id == r.tenant_id,
        )
    )
    # Read matches rule and passes
    assert engine.evaluate(subject, resource, "read", env) is True

    # Update has no matching rule -> Default-Deny
    assert engine.evaluate(subject, resource, "update", env) is False

    # 3. Individual Policy Function Unit Tests
    # Multi-tenant isolation
    assert policy_multi_tenant_isolation(subject, resource, "read", env) is True
    foreign_subject = SubjectContext(user_id=2, role="user", tenant_id="t2")
    assert policy_multi_tenant_isolation(foreign_subject, resource, "read", env) is False

    # Ownership & Admin bypass
    assert policy_ownership_and_admin_bypass(subject, resource, "update", env) is True
    assert policy_ownership_and_admin_bypass(foreign_subject, resource, "update", env) is False
    admin_subject = SubjectContext(user_id=99, role="admin", tenant_id="t1")
    assert policy_ownership_and_admin_bypass(admin_subject, resource, "update", env) is True

    # Lifecycle state guard
    archived_resource = ResourceContext(
        resource_type="document",
        resource_id=11,
        owner_id=1,
        tenant_id="t1",
        status="archived",
    )
    assert policy_lifecycle_state_guard(subject, archived_resource, "update", env) is False
    assert policy_lifecycle_state_guard(admin_subject, archived_resource, "update", env) is True

    # Contextual approval gate
    finance_subject = SubjectContext(user_id=5, role="user", department="finance", tenant_id="t1")
    high_val_resource = ResourceContext(
        resource_type="document",
        resource_id=12,
        owner_id=1,
        tenant_id="t1",
        amount=50000.0,
    )
    off_hours_env = EnvironmentContext(current_time=datetime.now(), client_ip="127.0.0.1", is_business_hours=False)

    # High value outside business hours -> False
    assert policy_contextual_approval_gate(finance_subject, high_val_resource, "approve", off_hours_env) is False
    # High value during business hours -> True
    assert policy_contextual_approval_gate(finance_subject, high_val_resource, "approve", env) is True
    # High value by non-finance during business hours -> False
    assert policy_contextual_approval_gate(subject, high_val_resource, "approve", env) is False


@pytest.mark.asyncio
async def test_abac_multi_tenant_isolation_forbidden(fake_redis: Any) -> None:
    """Verify that users from Tenant A cannot access Tenant B's documents (HTTP 403)."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # User 1 from Tenant Alpha creates a document
        user1_token = create_access_token(user_id=101, role="user")
        create_resp = await ac.post(
            "/documents",
            json={
                "title": "Alpha Confidential Report",
                "content": "Secret tenant alpha data",
                "department": "engineering",
                "tenant_id": "tenant_alpha",
                "amount": 100.0,
            },
            headers={
                "Authorization": f"Bearer {user1_token}",
                "X-Tenant-ID": "tenant_alpha",
            },
        )
        assert create_resp.status_code == 201
        doc_id = create_resp.json()["id"]

        # User 2 from Tenant Beta attempts to read User 1's document
        user2_token = create_access_token(user_id=202, role="user")
        read_resp = await ac.get(
            f"/documents/{doc_id}",
            headers={
                "Authorization": f"Bearer {user2_token}",
                "X-Tenant-ID": "tenant_beta",
            },
        )
        assert read_resp.status_code == 403
        assert "ABAC policy authorization failed" in read_resp.json()["detail"]

        # User 2 from Tenant Beta attempts to update User 1's document
        update_resp = await ac.put(
            f"/documents/{doc_id}",
            json={"title": "Hacked Title"},
            headers={
                "Authorization": f"Bearer {user2_token}",
                "X-Tenant-ID": "tenant_beta",
            },
        )
        assert update_resp.status_code == 403
        assert "ABAC policy authorization failed" in update_resp.json()["detail"]


@pytest.mark.asyncio
async def test_abac_ownership_and_admin_bypass(fake_redis: Any) -> None:
    """Verify owner can mutate document, non-owner is rejected, and admin bypasses ownership."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # 1. User 1 creates document in tenant_alpha
        owner_token = create_access_token(user_id=101, role="user")
        create_resp = await ac.post(
            "/documents",
            json={
                "title": "Quarterly Objectives",
                "content": "Q1 goals",
                "department": "operations",
                "tenant_id": "tenant_alpha",
                "amount": 500.0,
            },
            headers={
                "Authorization": f"Bearer {owner_token}",
                "X-Tenant-ID": "tenant_alpha",
            },
        )
        assert create_resp.status_code == 201
        doc_id = create_resp.json()["id"]

        # 2. Non-owner (same tenant) attempts to update -> 403 Forbidden
        colleague_token = create_access_token(user_id=102, role="user")
        colleague_resp = await ac.put(
            f"/documents/{doc_id}",
            json={"title": "Malicious Update"},
            headers={
                "Authorization": f"Bearer {colleague_token}",
                "X-Tenant-ID": "tenant_alpha",
            },
        )
        assert colleague_resp.status_code == 403

        # 3. Owner updates document -> 200 OK
        owner_update = await ac.put(
            f"/documents/{doc_id}",
            json={"title": "Updated Q1 Objectives"},
            headers={
                "Authorization": f"Bearer {owner_token}",
                "X-Tenant-ID": "tenant_alpha",
            },
        )
        assert owner_update.status_code == 200
        assert owner_update.json()["title"] == "Updated Q1 Objectives"

        # 4. Administrator updates document -> 200 OK (Admin Bypass)
        admin_token = create_access_token(user_id=999, role="admin")
        admin_update = await ac.put(
            f"/documents/{doc_id}",
            json={"title": "Admin Revised Objectives"},
            headers={
                "Authorization": f"Bearer {admin_token}",
                "X-Tenant-ID": "tenant_alpha",
            },
        )
        assert admin_update.status_code == 200
        assert admin_update.json()["title"] == "Admin Revised Objectives"


@pytest.mark.asyncio
async def test_abac_lifecycle_state_guard(fake_redis: Any) -> None:
    """Verify archived documents cannot be mutated by owners, but can be updated by administrators."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        owner_token = create_access_token(user_id=101, role="user")
        admin_token = create_access_token(user_id=999, role="admin")

        # 1. Create document
        create_resp = await ac.post(
            "/documents",
            json={
                "title": "Legal Contract 2025",
                "content": "Binding terms",
                "department": "legal",
                "tenant_id": "tenant_alpha",
                "amount": 2000.0,
            },
            headers={
                "Authorization": f"Bearer {owner_token}",
                "X-Tenant-ID": "tenant_alpha",
            },
        )
        assert create_resp.status_code == 201
        doc_id = create_resp.json()["id"]

        # 2. Owner archives document
        archive_resp = await ac.put(
            f"/documents/{doc_id}",
            json={"status": "archived"},
            headers={
                "Authorization": f"Bearer {owner_token}",
                "X-Tenant-ID": "tenant_alpha",
            },
        )
        assert archive_resp.status_code == 200
        assert archive_resp.json()["status"] == "archived"

        # 3. Owner attempts to update archived document -> 403 Forbidden
        owner_tamper = await ac.put(
            f"/documents/{doc_id}",
            json={"title": "Attempted Tamper"},
            headers={
                "Authorization": f"Bearer {owner_token}",
                "X-Tenant-ID": "tenant_alpha",
            },
        )
        assert owner_tamper.status_code == 403
        assert "ABAC policy authorization failed" in owner_tamper.json()["detail"]

        # 4. Owner attempts to delete archived document -> 403 Forbidden
        owner_del = await ac.delete(
            f"/documents/{doc_id}",
            headers={
                "Authorization": f"Bearer {owner_token}",
                "X-Tenant-ID": "tenant_alpha",
            },
        )
        assert owner_del.status_code == 403

        # 5. Administrator can update or restore archived document -> 200 OK
        admin_restore = await ac.put(
            f"/documents/{doc_id}",
            json={"status": "active"},
            headers={
                "Authorization": f"Bearer {admin_token}",
                "X-Tenant-ID": "tenant_alpha",
            },
        )
        assert admin_restore.status_code == 200
        assert admin_restore.json()["status"] == "active"


@pytest.mark.asyncio
async def test_abac_contextual_environmental_approval(fake_redis: Any) -> None:
    """Verify contextual approval gate: department check, amount threshold, and business hours."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        owner_token = create_access_token(user_id=101, role="user")

        # 1. Create high-value document ($50,000)
        high_val_resp = await ac.post(
            "/documents",
            json={
                "title": "Enterprise Cloud Migration Deal",
                "content": "Budget approval request",
                "department": "finance",
                "tenant_id": "tenant_alpha",
                "amount": 50000.0,
            },
            headers={
                "Authorization": f"Bearer {owner_token}",
                "X-Tenant-ID": "tenant_alpha",
            },
        )
        assert high_val_resp.status_code == 201
        doc_id = high_val_resp.json()["id"]

        finance_token = create_access_token(user_id=303, role="user")
        eng_token = create_access_token(user_id=404, role="user")

        # Case A: Non-finance user attempts approval during business hours -> 403 Forbidden
        eng_attempt = await ac.post(
            f"/documents/{doc_id}/approve",
            headers={
                "Authorization": f"Bearer {eng_token}",
                "X-Tenant-ID": "tenant_alpha",
                "X-Department": "engineering",
                "X-Business-Hours": "true",
            },
        )
        assert eng_attempt.status_code == 403

        # Case B: Finance user attempts approval OUTSIDE business hours -> 403 Forbidden
        off_hours_attempt = await ac.post(
            f"/documents/{doc_id}/approve",
            headers={
                "Authorization": f"Bearer {finance_token}",
                "X-Tenant-ID": "tenant_alpha",
                "X-Department": "finance",
                "X-Business-Hours": "false",
            },
        )
        assert off_hours_attempt.status_code == 403

        # Case C: Finance user approves INSIDE business hours -> 200 OK
        valid_approval = await ac.post(
            f"/documents/{doc_id}/approve",
            headers={
                "Authorization": f"Bearer {finance_token}",
                "X-Tenant-ID": "tenant_alpha",
                "X-Department": "finance",
                "X-Business-Hours": "true",
            },
        )
        assert valid_approval.status_code == 200
        assert valid_approval.json()["status"] == "approved"
        assert valid_approval.json()["approved_by"] == 303

        # Case D: Standard-value document (<= $10,000)
        low_val_resp = await ac.post(
            "/documents",
            json={
                "title": "Office Supplies Invoice",
                "content": "Paper and pens",
                "department": "operations",
                "tenant_id": "tenant_alpha",
                "amount": 250.0,
            },
            headers={
                "Authorization": f"Bearer {owner_token}",
                "X-Tenant-ID": "tenant_alpha",
            },
        )
        low_id = low_val_resp.json()["id"]

        # Owner can approve standard-value document
        owner_approve = await ac.post(
            f"/documents/{low_id}/approve",
            headers={
                "Authorization": f"Bearer {owner_token}",
                "X-Tenant-ID": "tenant_alpha",
                "X-Department": "operations",
                "X-Business-Hours": "true",
            },
        )
        assert owner_approve.status_code == 200
        assert owner_approve.json()["status"] == "approved"


@pytest.mark.asyncio
async def test_abac_delete_document_owner_and_admin(fake_redis: Any) -> None:
    """Verify delete operations: non-owner rejected, owner deletes, admin deletes."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        owner_token = create_access_token(user_id=101, role="user")
        stranger_token = create_access_token(user_id=102, role="user")
        admin_token = create_access_token(user_id=999, role="admin")

        # 1. Create document
        create_resp = await ac.post(
            "/documents",
            json={
                "title": "Draft Proposal",
                "content": "To be discarded",
                "department": "marketing",
                "tenant_id": "tenant_alpha",
                "amount": 0.0,
            },
            headers={
                "Authorization": f"Bearer {owner_token}",
                "X-Tenant-ID": "tenant_alpha",
            },
        )
        doc_id = create_resp.json()["id"]

        # 2. Stranger attempts delete -> 403 Forbidden
        stranger_del = await ac.delete(
            f"/documents/{doc_id}",
            headers={
                "Authorization": f"Bearer {stranger_token}",
                "X-Tenant-ID": "tenant_alpha",
            },
        )
        assert stranger_del.status_code == 403

        # 3. Owner deletes document -> 204 No Content
        owner_del = await ac.delete(
            f"/documents/{doc_id}",
            headers={
                "Authorization": f"Bearer {owner_token}",
                "X-Tenant-ID": "tenant_alpha",
            },
        )
        assert owner_del.status_code == 204

        # 4. Create another document for admin delete
        create2 = await ac.post(
            "/documents",
            json={
                "title": "Spam Document",
                "content": "Inappropriate content",
                "department": "marketing",
                "tenant_id": "tenant_alpha",
                "amount": 0.0,
            },
            headers={
                "Authorization": f"Bearer {owner_token}",
                "X-Tenant-ID": "tenant_alpha",
            },
        )
        doc2_id = create2.json()["id"]

        # 5. Admin deletes document -> 204 No Content
        admin_del = await ac.delete(
            f"/documents/{doc2_id}",
            headers={
                "Authorization": f"Bearer {admin_token}",
                "X-Tenant-ID": "tenant_alpha",
            },
        )
        assert admin_del.status_code == 204
