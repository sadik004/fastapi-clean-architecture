# RCA: Day 46 - ABAC Resource Resolution NoneType Checks & Default-Deny Policy Invariants

- **Date**: 2026-09-09
- **Trigger**: During Day 46 implementation of the Attribute-Based Access Control (ABAC) Policy Engine and document management endpoints:
  1. `AttributeError: 'NoneType' object has no attribute 'tenant_id'` in `check_abac_permission` dependency when non-existent document IDs were queried.
  2. Potential unauthorized bypass when resource context evaluation ran before verifying resource existence.
  3. `KeyError` or permissive fallback when undefined actions (e.g. `"publish"`, `"archive"`) were supplied to `PolicyEngine.evaluate()`.

- **Faulty Code / Pattern**:
  ```python
  # FLAW 1: Passing unvalidated resource loader results directly to context builder
  async def check_abac_permission(action: str, resource_loader: Callable[..., Awaitable[Any]]):
      async def dependency(request: Request, user: UserEntity = Depends(...)):
          resource = await resource_loader(request)
          # If document does not exist, resource is None!
          resource_ctx = ResourceContext(
              resource_type="document",
              resource_id=resource.id,        # AttributeError: 'NoneType' object has no attribute 'id'
              owner_id=resource.owner_id,     # Crashes with 500 instead of returning clean 404
              tenant_id=resource.tenant_id,
              ...
          )
          ...

  # FLAW 2: Soft default or non-boolean return in policy evaluation
  class PolicyEngine:
      def evaluate(self, subject, resource, action, env) -> bool:
          rules = self._rules.get((resource.resource_type, action), [])
          if not rules:
              return True  # CATASTROPHIC: Permissive default permits unregistered actions!
          return all(rule.predicate(subject, resource, action, env) for rule in rules)
  ```

- **Root Cause**:
  1. **Premature Context Construction on Non-Existent Resources**:
     In a declarative FastAPI authorization guard (`check_abac_permission`), resolving the resource from storage is asynchronous. If the client queries an invalid or deleted ID (`GET /documents/99999`), the repository loader returns `None`. Constructing `ResourceContext` from `None` attributes raises an unhandled `AttributeError`, causing an internal server 500 error instead of a clean HTTP 404 Not Found.
  2. **Violation of Strict Default-Deny (Least Privilege)**:
     In enterprise authorization systems, unmapped operations or missing policies must never default to permissive access. If `(resource_type, action)` has no registered policies, or if any policy predicate fails, the engine must strictly return `False` (HTTP 403 Forbidden).
  3. **Conjunctive Evaluation Ordering**:
     Tenant isolation (`subject.tenant_id == resource.tenant_id`) must always precede ownership checks or admin bypasses. If an admin from Tenant A attempts to access resources belonging to Tenant B, multi-tenant boundaries must take strict precedence over administrative roles.

- **Resolution**:
  ```python
  # 1. Defensive 404 Guard in check_abac_permission (app/core/dependencies.py)
  async def check_abac_permission(
      action: str,
      resource_loader: Callable[[Request], Awaitable[ResourceContext | Any]],
  ) -> Callable[..., Awaitable[None]]:
      async def dependency(
          request: Request,
          user: UserEntity = Depends(get_current_authenticated_user),
          engine: PolicyEngine = Depends(get_policy_engine),
      ) -> None:
          resource_raw = await resource_loader(request)
          if resource_raw is None:
              raise HTTPException(status_code=404, detail="Resource not found")
          
          # Safely map to ResourceContext once existence is guaranteed
          if isinstance(resource_raw, ResourceContext):
              resource_ctx = resource_raw
          else:
              resource_ctx = ResourceContext(
                  resource_type="document",
                  resource_id=resource_raw.id,
                  owner_id=resource_raw.owner_id,
                  tenant_id=resource_raw.tenant_id,
                  department=resource_raw.department,
                  status=resource_raw.status,
                  amount=resource_raw.amount,
              )
          
          # 2. Strict Default-Deny Policy Evaluation
          allowed = engine.evaluate(subject_ctx, resource_ctx, action, env_ctx)
          if not allowed:
              raise HTTPException(
                  status_code=403,
                  detail=f"Forbidden: ABAC policy denied action '{action}' on resource",
              )

  # 3. Conjunctive Strict Default-Deny Engine (app/core/abac.py)
  class PolicyEngine:
      def evaluate(
          self,
          subject: SubjectContext,
          resource: ResourceContext,
          action: str,
          environment: EnvironmentContext,
      ) -> bool:
          matching_rules = [
              r for r in self._rules
              if r.resource_type == resource.resource_type and r.action == action
          ]
          # Least Privilege Invariant: Unregistered rules evaluate strictly to False
          if not matching_rules:
              return False
          
          # Conjunctive evaluation: Every rule predicate must evaluate to True
          for rule in matching_rules:
              if not bool(rule.predicate(subject, resource, action, environment)):
                  return False
          return True
  ```

- **Permanent Prevention Rules**:
  - *Rule 85*: When developing declarative resource-bound authorization dependencies (`check_abac_permission`), always validate that the resource exists before constructing authorization contexts; return HTTP 404 if `resource is None`.
  - *Rule 86*: ABAC policy engines must unconditionally enforce Default-Deny (Least Privilege): empty matching rule sets and unhandled actions must evaluate strictly to `False`.
