"""Automated CI/CD Pipeline Quality Gate Tests.

Validates:
1. GitHub Actions workflow YAML syntax, job structure, and triggers.
2. Job Dependency Directed Acyclic Graph (DAG) convergence invariants.
3. Multi-stage quality gates (Ruff, Mypy, Bandit, Semgrep, Architecture Linter, Pytest).
4. Local CI verification runner script integrity and stage completeness.
5. Production multi-stage Dockerfile hardening contract.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]

PROJECT_ROOT = Path(__file__).resolve().parent.parent
WORKFLOW_FILE = PROJECT_ROOT / ".github" / "workflows" / "ci.yml"
RUNNER_SCRIPT = PROJECT_ROOT / "scripts" / "run_ci_locally.sh"
DOCKERFILE_PATH = PROJECT_ROOT / "Dockerfile"


def load_workflow_yaml() -> dict[str, Any]:
    """Parse and return the GitHub Actions workflow definition."""
    assert WORKFLOW_FILE.exists(), f"Workflow file missing: {WORKFLOW_FILE}"
    with open(WORKFLOW_FILE, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    assert isinstance(data, dict), "Parsed workflow YAML must be a dictionary"
    return data


def test_workflow_yaml_validity() -> None:
    """Assert .github/workflows/ci.yml is syntactically valid YAML."""
    data = load_workflow_yaml()
    assert "name" in data, "Workflow must declare a 'name'"
    assert "jobs" in data, "Workflow must declare 'jobs'"
    assert isinstance(data["jobs"], dict), "'jobs' section must be a dictionary"
    assert len(data["jobs"]) >= 6, "Workflow must contain at least 6 stages"


def test_pipeline_trigger_branches() -> None:
    """Assert pipeline triggers exclusively on main for push and PRs."""
    data = load_workflow_yaml()
    raw_triggers: Any = data.get("on")
    if raw_triggers is None:
        raw_triggers = data.get(True)  # type: ignore[call-overload]
    assert isinstance(raw_triggers, dict), "Workflow must declare 'on' trigger mapping"
    triggers: dict[str, Any] = raw_triggers

    assert "push" in triggers, "Workflow must trigger on git push"
    assert triggers["push"].get("branches") == ["main"], "Push trigger must target main"

    assert "pull_request" in triggers, "Workflow must trigger on pull request"
    assert triggers["pull_request"].get("branches") == ["main"], "PR trigger must target main"


def test_job_dependency_dag_invariant() -> None:
    """Assert docker-build executes ONLY if all 5 quality gate jobs pass."""
    data = load_workflow_yaml()
    jobs = data["jobs"]

    expected_stages = {
        "lint",
        "type-check",
        "security-audit",
        "architecture-audit",
        "test-suite",
        "docker-build",
    }
    assert expected_stages.issubset(jobs.keys()), f"Missing jobs in {jobs.keys()}"

    docker_build_job = jobs["docker-build"]
    assert "needs" in docker_build_job, "docker-build must declare 'needs' dependencies"

    declared_needs = set(docker_build_job["needs"])
    required_upstream_gates = {
        "lint",
        "type-check",
        "security-audit",
        "architecture-audit",
        "test-suite",
    }
    assert declared_needs == required_upstream_gates, (
        f"docker-build must depend on all 5 upstream gates. Found: {declared_needs}, Expected: {required_upstream_gates}"
    )


def test_quality_gate_matrix_steps() -> None:
    """Assert each job executes the required production toolchain commands."""
    data = load_workflow_yaml()
    jobs = data["jobs"]

    # Helper to extract all 'run' commands from a job
    def get_run_commands(job_key: str) -> list[str]:
        job = jobs[job_key]
        return [step["run"] for step in job.get("steps", []) if "run" in step]

    # Stage 1: Lint
    lint_cmds = " ".join(get_run_commands("lint"))
    assert "ruff check" in lint_cmds, "Lint job must run ruff check"
    assert "ruff format --check" in lint_cmds, "Lint job must verify ruff formatting"

    # Stage 2: Type Check
    type_cmds = " ".join(get_run_commands("type-check"))
    assert "mypy --strict" in type_cmds, "Type check job must execute strict mypy"

    # Stage 3: Security Audit
    sec_cmds = " ".join(get_run_commands("security-audit"))
    assert "bandit -r app -ll" in sec_cmds, "Security job must execute bandit with -ll"
    assert "semgrep scan" in sec_cmds, "Security job must execute semgrep"

    # Stage 4: Architecture Audit
    arch_cmds = " ".join(get_run_commands("architecture-audit"))
    assert "scripts/audit_architecture.py" in arch_cmds, "Architecture job must execute audit_architecture.py"

    # Stage 5: Test Suite
    test_cmds = " ".join(get_run_commands("test-suite"))
    assert "pytest" in test_cmds, "Test suite job must execute pytest"

    # Stage 6: Docker Build
    docker_cmds = " ".join(get_run_commands("docker-build"))
    assert "docker build" in docker_cmds, "Docker job must execute docker build"
    assert "10001" in docker_cmds, "Docker job must verify non-root UID 10001"


def test_local_runner_script_integrity() -> None:
    """Assert scripts/run_ci_locally.sh exists and contains all stage invocations."""
    assert RUNNER_SCRIPT.exists(), f"Local runner script missing: {RUNNER_SCRIPT}"
    content = RUNNER_SCRIPT.read_text(encoding="utf-8")

    assert "set -e" in content, "Local runner script must enforce fail-fast with 'set -e'"
    assert "ruff check" in content, "Local runner must execute ruff check"
    assert "ruff format --check" in content, "Local runner must execute ruff format check"
    assert "mypy --strict" in content, "Local runner must execute strict mypy"
    assert "bandit -r app -ll" in content, "Local runner must execute bandit"
    assert "semgrep scan" in content, "Local runner must execute semgrep"
    assert "scripts/audit_architecture.py" in content, "Local runner must execute architecture audit"
    assert "pytest" in content, "Local runner must execute test suite"
    assert "docker build" in content, "Local runner must execute docker build"


def test_dockerfile_multi_stage_contract() -> None:
    """Assert production Dockerfile enforces multi-stage builder and non-root runner."""
    assert DOCKERFILE_PATH.exists(), f"Dockerfile missing: {DOCKERFILE_PATH}"
    content = DOCKERFILE_PATH.read_text(encoding="utf-8")

    assert "AS builder" in content, "Dockerfile must declare multi-stage builder"
    assert "AS runner" in content, "Dockerfile must declare hardened runtime runner"
    assert "10001" in content, "Dockerfile must configure non-root user UID 10001"
    assert "USER appuser:appgroup" in content, "Dockerfile must switch to non-root appuser"
    assert "HEALTHCHECK" in content, "Dockerfile must specify container HEALTHCHECK"
