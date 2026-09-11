from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
DOCKERFILE_PATH = REPO_ROOT / "Dockerfile"
DOCKERIGNORE_PATH = REPO_ROOT / ".dockerignore"


def test_dockerfile_exists() -> None:
    """Verify Dockerfile exists at repository root."""
    assert DOCKERFILE_PATH.is_file(), f"Dockerfile not found at {DOCKERFILE_PATH}"


def test_dockerignore_exists() -> None:
    """Verify .dockerignore exists at repository root."""
    assert DOCKERIGNORE_PATH.is_file(), f".dockerignore not found at {DOCKERIGNORE_PATH}"


def test_multistage_builder_and_runner_stages() -> None:
    """Invariant: Dockerfile must define at least 2 stages (builder and runner).

    The compiler toolchain must be confined to the builder stage to eliminate image bloat.
    """
    content = DOCKERFILE_PATH.read_text(encoding="utf-8")
    from_lines = [line.strip() for line in content.splitlines() if line.strip().upper().startswith("FROM ")]

    assert len(from_lines) >= 2, f"Expected at least 2 FROM instructions, found {len(from_lines)}"
    assert any("AS BUILDER" in line.upper() for line in from_lines), "Missing 'AS builder' stage"
    assert any("AS RUNNER" in line.upper() for line in from_lines), "Missing 'AS runner' stage"

    # Verify base image is Debian slim-based
    assert all("SLIM" in line.upper() for line in from_lines), "All stages should use slim images"


def test_layer_caching_dependency_copy_precedes_application_code() -> None:
    """Invariant: Dependency specifications must be copied and installed BEFORE application code.

    This maximizes Docker layer caching: code changes should not trigger dependency reinstallations.
    """
    content = DOCKERFILE_PATH.read_text(encoding="utf-8")
    lines = content.splitlines()

    req_copy_index = -1
    app_copy_index = -1

    for idx, line in enumerate(lines):
        clean = line.strip().lower()
        if clean.startswith("copy") and "requirements.txt" in clean:
            req_copy_index = idx
        if clean.startswith("copy") and "app /app/app" in clean:
            app_copy_index = idx

    assert req_copy_index != -1, "requirements.txt must be copied"
    assert app_copy_index != -1, "Application code must be copied"
    assert req_copy_index < app_copy_index, (
        f"Layer caching violation: requirements.txt (line {req_copy_index + 1}) "
        f"must be copied before application source (line {app_copy_index + 1})"
    )


def test_non_root_user_and_group_enforcement() -> None:
    """CIS Docker Benchmark Invariant: Containers must run as an unprivileged non-root user.

    Prevents Container Escape vulnerabilities and root privilege escalation on the host kernel.
    """
    content = DOCKERFILE_PATH.read_text(encoding="utf-8")

    # Verify unprivileged group and user creation with UID/GID 10001
    assert "groupadd -g 10001" in content or "10001" in content, "Missing unprivileged GID 10001 creation"
    assert "useradd -u 10001" in content or "appuser" in content, "Missing unprivileged UID 10001 user creation"
    assert "/sbin/nologin" in content, "Non-root user must be assigned a nologin shell"

    # Find the final active USER instruction
    user_lines = [line.strip() for line in content.splitlines() if line.strip().upper().startswith("USER ")]
    assert len(user_lines) >= 1, "Dockerfile must define an explicit USER instruction"

    final_user = user_lines[-1]
    assert "root" not in final_user.lower(), f"Final user must NOT be root: {final_user}"
    assert "appuser" in final_user.lower() or "10001" in final_user, f"Unexpected final user: {final_user}"


def test_toolchain_isolation_and_virtualenv_copy() -> None:
    """Invariant: Compilers (gcc, python3-dev) must NEVER exist in the runner stage.

    Only the compiled virtualenv (/opt/venv) is copied into the runner.
    """
    content = DOCKERFILE_PATH.read_text(encoding="utf-8")
    stages = content.split("FROM ")
    assert len(stages) >= 3, "Expected at least 2 distinct stages separated by FROM"

    builder_stage = stages[1]
    runner_stage = stages[2]

    # Builder stage contains compilers
    assert "gcc" in builder_stage.lower(), "Builder stage should install gcc for C-extensions"
    assert "python -m venv /opt/venv" in builder_stage, "Builder stage must create /opt/venv"

    # Runner stage does not install gcc
    assert "gcc" not in runner_stage.lower(), "Security violation: gcc found in runtime runner stage!"
    assert "COPY --from=builder /opt/venv /opt/venv" in runner_stage, (
        "Runner stage must copy compiled /opt/venv from builder"
    )
    assert 'PATH="/opt/venv/bin:$PATH"' in runner_stage or "PATH=/opt/venv/bin:$PATH" in runner_stage, (
        "Runner stage must add /opt/venv/bin to PATH"
    )


def test_healthcheck_directive_points_to_liveness_probe() -> None:
    """Invariant: Dockerfile must define an active HEALTHCHECK pointing to the Day 75 in-memory probe."""
    content = DOCKERFILE_PATH.read_text(encoding="utf-8")
    assert "HEALTHCHECK" in content, "Dockerfile must define a HEALTHCHECK instruction"
    assert "/health/liveness" in content, "HEALTHCHECK must point to /health/liveness probe"
    assert "--interval=" in content, "HEALTHCHECK must specify --interval"
    assert "--timeout=" in content, "HEALTHCHECK must specify --timeout"
    assert "--start-period=" in content, "HEALTHCHECK must specify --start-period"
    assert "--retries=" in content, "HEALTHCHECK must specify --retries"


def test_cmd_runs_uvicorn_production_server() -> None:
    """Invariant: Dockerfile CMD must start uvicorn targeting app.main:app."""
    content = DOCKERFILE_PATH.read_text(encoding="utf-8")
    cmd_lines = [line.strip() for line in content.splitlines() if line.strip().upper().startswith("CMD ")]
    assert len(cmd_lines) >= 1, "Dockerfile must define a CMD instruction"
    final_cmd = cmd_lines[-1]
    assert "uvicorn" in final_cmd, f"CMD must run uvicorn: {final_cmd}"
    assert "app.main:app" in final_cmd, f"CMD must target app.main:app: {final_cmd}"
    assert "8000" in final_cmd, f"CMD must bind to port 8000: {final_cmd}"
    assert "EXPOSE 8000" in content, "Dockerfile must expose port 8000"


def test_dockerignore_excludes_sensitive_files_and_caches() -> None:
    """Invariant: .dockerignore must strictly prevent secret leakage and cache pollution."""
    content = DOCKERIGNORE_PATH.read_text(encoding="utf-8")
    ignored_patterns = {
        line.strip() for line in content.splitlines() if line.strip() and not line.strip().startswith("#")
    }

    # Secrets protection
    assert any(".env" in p for p in ignored_patterns), ".dockerignore must exclude .env files"

    # Version control & internal systems
    assert any(".git" in p for p in ignored_patterns), ".dockerignore must exclude .git"

    # Virtual environments
    assert any(".venv" in p or "venv" in p for p in ignored_patterns), ".dockerignore must exclude .venv"

    # Test suites & documentation (eliminate bloat)
    assert "tests" in ignored_patterns, ".dockerignore must exclude tests"
    assert "docs" in ignored_patterns, ".dockerignore must exclude docs"

    # Python bytecode & caches
    assert any("__pycache__" in p for p in ignored_patterns), ".dockerignore must exclude __pycache__"
    assert any(".pytest_cache" in p for p in ignored_patterns), ".dockerignore must exclude .pytest_cache"
    assert any(".mypy_cache" in p for p in ignored_patterns), ".dockerignore must exclude .mypy_cache"
    assert any(".ruff_cache" in p for p in ignored_patterns), ".dockerignore must exclude .ruff_cache"

    # Local SQLite databases
    assert any("*.db" in p or "app.db" in p for p in ignored_patterns), ".dockerignore must exclude local databases"
