"""Automated Codebase Compliance and Static Type Safety Test Suite.

Verifies:
1. Zero print() statements in production code (app/).
2. pyproject.toml configuration integrity (mypy --strict and ruff rules).
3. Explicit type annotations across all domain services and repository protocols.
4. Complete AST inspection ensuring zero unannotated functions in production code.
"""

import ast
import inspect
import tomllib
from pathlib import Path

from app.repositories.user_repository import UserRepositoryProtocol
from app.services.user_service import UserService

WORKSPACE_ROOT = Path(__file__).resolve().parent.parent
APP_DIR = WORKSPACE_ROOT / "app"
PYPROJECT_PATH = WORKSPACE_ROOT / "pyproject.toml"


# ==============================================================================
# 1. Zero Production print() Invariant
# ==============================================================================


def test_no_production_print_statements() -> None:
    """Ensure no print() calls exist anywhere inside the app/ directory."""
    violations: list[str] = []

    for py_file in APP_DIR.rglob("*.py"):
        try:
            tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
        except Exception as exc:
            violations.append(f"{py_file}: failed to parse AST: {exc}")
            continue

        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                if isinstance(func, ast.Name) and func.id == "print":
                    violations.append(f"{py_file.relative_to(WORKSPACE_ROOT)}:{node.lineno}: print() call detected")

    assert not violations, "Production print() statements forbidden under T201:\n" + "\n".join(violations)


# ==============================================================================
# 2. pyproject.toml Tooling Configuration Integrity
# ==============================================================================


def test_pyproject_toml_configuration_integrity() -> None:
    """Verify pyproject.toml enforces zero-tolerance mypy --strict and ruff rules."""
    assert PYPROJECT_PATH.exists(), "pyproject.toml must exist at workspace root"

    with open(PYPROJECT_PATH, "rb") as f:
        config = tomllib.load(f)

    tool_section = config.get("tool", {})

    # Mypy strict configuration validation
    mypy_cfg = tool_section.get("mypy", {})
    assert mypy_cfg.get("strict") is True, "[tool.mypy] strict must be true"
    assert mypy_cfg.get("disallow_untyped_defs") is True, "[tool.mypy] disallow_untyped_defs must be true"
    assert mypy_cfg.get("disallow_any_generics") is True, "[tool.mypy] disallow_any_generics must be true"
    assert mypy_cfg.get("warn_unused_ignores") is True, "[tool.mypy] warn_unused_ignores must be true"
    assert mypy_cfg.get("warn_return_any") is True, "[tool.mypy] warn_return_any must be true"
    assert mypy_cfg.get("no_implicit_reexport") is True, "[tool.mypy] no_implicit_reexport must be true"

    # Ruff rules validation
    ruff_lint = tool_section.get("ruff", {}).get("lint", {})
    selected_rules: list[str] = ruff_lint.get("select", [])
    for rule in ["E", "F", "W", "I", "UP", "B", "S", "T201"]:
        assert rule in selected_rules, f"Ruff must select rule '{rule}' for strict linting"


# ==============================================================================
# 3. Domain Protocol & Service Type Signature Verification
# ==============================================================================


def test_user_repository_protocol_type_completeness() -> None:
    """Verify that all methods on UserRepositoryProtocol have explicit type annotations."""
    required_methods = ["create", "get_by_id", "get_by_email", "get_by_username", "update", "delete", "list_all"]

    for method_name in required_methods:
        method = getattr(UserRepositoryProtocol, method_name, None)
        assert method is not None, f"UserRepositoryProtocol missing required method '{method_name}'"

        sig = inspect.signature(method)
        assert sig.return_annotation is not inspect.Signature.empty, (
            f"Method '{method_name}' on UserRepositoryProtocol missing return type annotation"
        )
        for param_name, param in sig.parameters.items():
            if param_name == "self":
                continue
            assert param.annotation is not inspect.Signature.empty, (
                f"Parameter '{param_name}' of '{method_name}' on UserRepositoryProtocol missing type annotation"
            )


def test_user_service_method_type_completeness() -> None:
    """Verify that all public methods on UserService have explicit type annotations."""
    for attr_name in dir(UserService):
        if attr_name.startswith("_"):
            continue
        attr = getattr(UserService, attr_name)
        if callable(attr):
            sig = inspect.signature(attr)
            assert sig.return_annotation is not inspect.Signature.empty, (
                f"Public method '{attr_name}' on UserService missing return type annotation"
            )
            for param_name, param in sig.parameters.items():
                if param_name == "self":
                    continue
                assert param.annotation is not inspect.Signature.empty, (
                    f"Parameter '{param_name}' on UserService.{attr_name} missing type annotation"
                )


# ==============================================================================
# 4. AST Return Annotation Enforcement Across Entire app/ Package
# ==============================================================================


def test_all_app_functions_have_explicit_return_annotations() -> None:
    """Traverse all AST function/async function definitions in app/ to guarantee return types."""
    unannotated: list[str] = []

    for py_file in APP_DIR.rglob("*.py"):
        tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                # Special dunder methods like __init__ may omit return annotation in some standards,
                # but under strict typing, -> None is required or expected.
                if node.returns is None:
                    unannotated.append(
                        f"{py_file.relative_to(WORKSPACE_ROOT)}:{node.lineno}: function '{node.name}' has no return annotation"
                    )

    assert not unannotated, "Functions without explicit return type annotations detected in app/:\n" + "\n".join(
        unannotated
    )
