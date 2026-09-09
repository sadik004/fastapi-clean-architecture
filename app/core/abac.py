"""Attribute-Based Access Control (ABAC) Architecture & Policy-Driven Permission Engine.

Evaluates authorization dynamically across 4 core dimensions:
1. Subject: Who is requesting access (user_id, role, department, tenant_id).
2. Resource: What is being accessed (resource_type, resource_id, owner_id, tenant_id, department, status, amount).
3. Action: What operation is requested (read, update, delete, approve).
4. Environment: Contextual conditions (current_time, client_ip, is_business_hours).
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass(slots=True)
class SubjectContext:
    """Attributes defining the actor requesting access."""

    user_id: int
    role: str
    department: str | None = None
    tenant_id: str | None = None


@dataclass(slots=True)
class ResourceContext:
    """Attributes defining the target entity or document."""

    resource_type: str
    resource_id: Any
    owner_id: int
    tenant_id: str | None = None
    department: str | None = None
    status: str = "active"
    amount: float | None = None


@dataclass(slots=True)
class EnvironmentContext:
    """Attributes defining the execution context of the request."""

    current_time: datetime
    client_ip: str
    is_business_hours: bool = True


@dataclass(slots=True)
class PolicyRule:
    """A policy rule binding a predicate to a resource type and action."""

    name: str
    resource_type: str
    action: str  # specific action (e.g. 'update', 'approve') or '*' for all actions
    predicate: Callable[[SubjectContext, ResourceContext, str, EnvironmentContext], bool]


class PolicyEngine:
    """High-performance in-memory policy engine enforcing Default-Deny ABAC evaluation.

    Evaluates rules in O(P) time where P is the number of matching rules.
    """

    def __init__(self) -> None:
        self._rules: dict[str, list[PolicyRule]] = defaultdict(list)

    def register_rule(self, rule: PolicyRule) -> None:
        """Register a policy rule for a specific resource type."""
        self._rules[rule.resource_type].append(rule)

    def evaluate(
        self,
        subject: SubjectContext,
        resource: ResourceContext,
        action: str,
        environment: EnvironmentContext,
    ) -> bool:
        """Evaluate registered policy rules for the given context.

        Default-Deny Invariant:
        - If zero matching rules exist for (resource_type, action), access is strictly DENIED (False).
        - If matching rules exist, ALL matching rules must evaluate to True (Conjunctive evaluation).
        - If any matching rule evaluates to False, access is strictly DENIED (False).
        """
        registered = self._rules.get(resource.resource_type, [])
        matching_rules = [rule for rule in registered if rule.action == "*" or rule.action == action]

        if not matching_rules:
            # Default-Deny: No explicit policy grants access
            return False

        for rule in matching_rules:
            if not rule.predicate(subject, resource, action, environment):
                return False

        return True


# ---------------------------------------------------------------------------
# Concrete Enterprise Policy Predicates
# ---------------------------------------------------------------------------


def policy_multi_tenant_isolation(
    subject: SubjectContext,
    resource: ResourceContext,
    action: str,
    environment: EnvironmentContext,
) -> bool:
    """Policy 1: Multi-Tenant Isolation.

    Users can never access another tenant's resources.
    Both tenant IDs must match and must not be None.
    """
    if subject.tenant_id is None or resource.tenant_id is None:
        return False
    return subject.tenant_id == resource.tenant_id


def policy_ownership_and_admin_bypass(
    subject: SubjectContext,
    resource: ResourceContext,
    action: str,
    environment: EnvironmentContext,
) -> bool:
    """Policy 2: Ownership & Admin Bypass.

    Only the owner of the resource or an admin can mutate or operate on the resource.
    """
    if subject.role == "admin":
        return True
    return subject.user_id == resource.owner_id


def policy_lifecycle_state_guard(
    subject: SubjectContext,
    resource: ResourceContext,
    action: str,
    environment: EnvironmentContext,
) -> bool:
    """Policy 3: Resource Lifecycle State Guard.

    If resource is 'archived', mutation ('update' or 'delete') is strictly forbidden,
    even for the owner, unless the subject is an 'admin'.
    """
    if resource.status == "archived":
        return subject.role == "admin"
    return True


def policy_contextual_approval_gate(
    subject: SubjectContext,
    resource: ResourceContext,
    action: str,
    environment: EnvironmentContext,
) -> bool:
    """Policy 4: Contextual / Environmental Approval Gate.

    If action == 'approve' and resource.amount > 10,000:
      Requires subject.department == 'finance' AND environment.is_business_hours is True.
    If resource.amount <= 10,000:
      Requires subject.department == 'finance' or subject.role == 'admin' or subject.user_id == resource.owner_id.
    """
    if action == "approve":
        if resource.amount is not None and resource.amount > 10000.0:
            return subject.department == "finance" and environment.is_business_hours is True
        # Standard approval threshold (<= 10,000)
        return subject.department == "finance" or subject.role == "admin" or subject.user_id == resource.owner_id
    return True


def policy_read_access(
    subject: SubjectContext,
    resource: ResourceContext,
    action: str,
    environment: EnvironmentContext,
) -> bool:
    """Policy for Read Operations.

    Requires tenant isolation (handled by tenant rule) and permits read access
    if user belongs to the same tenant.
    """
    return True


def create_default_policy_engine() -> PolicyEngine:
    """Create and configure a production PolicyEngine with enterprise policies for 'document'."""
    engine = PolicyEngine()

    # Document Resource Policies:
    # 1. Multi-Tenant Isolation applies to all actions on documents
    engine.register_rule(
        PolicyRule(
            name="document_multi_tenant_isolation",
            resource_type="document",
            action="*",
            predicate=policy_multi_tenant_isolation,
        )
    )

    # 2. Read access
    engine.register_rule(
        PolicyRule(
            name="document_read_access",
            resource_type="document",
            action="read",
            predicate=policy_read_access,
        )
    )

    # 3. Ownership & Admin Bypass for update and delete
    engine.register_rule(
        PolicyRule(
            name="document_update_ownership",
            resource_type="document",
            action="update",
            predicate=policy_ownership_and_admin_bypass,
        )
    )
    engine.register_rule(
        PolicyRule(
            name="document_delete_ownership",
            resource_type="document",
            action="delete",
            predicate=policy_ownership_and_admin_bypass,
        )
    )

    # 4. Lifecycle state guard for update and delete
    engine.register_rule(
        PolicyRule(
            name="document_update_lifecycle_guard",
            resource_type="document",
            action="update",
            predicate=policy_lifecycle_state_guard,
        )
    )
    engine.register_rule(
        PolicyRule(
            name="document_delete_lifecycle_guard",
            resource_type="document",
            action="delete",
            predicate=policy_lifecycle_state_guard,
        )
    )

    # 5. Contextual / Environmental approval gate
    engine.register_rule(
        PolicyRule(
            name="document_contextual_approval_gate",
            resource_type="document",
            action="approve",
            predicate=policy_contextual_approval_gate,
        )
    )

    return engine


_default_policy_engine: PolicyEngine | None = None


def get_default_policy_engine() -> PolicyEngine:
    """Singleton provider for the default ABAC PolicyEngine."""
    global _default_policy_engine
    if _default_policy_engine is None:
        _default_policy_engine = create_default_policy_engine()
    return _default_policy_engine
