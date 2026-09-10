# ==============================================================================
# Production Multi-Stage Dockerfile for FastAPI Clean Architecture
# Stage 1: Compile-time Builder (gcc, build headers, virtualenv creation)
# Stage 2: Hardened Minimal Runtime (Non-root user UID 10001, no compilers)
# ==============================================================================

# ------------------------------------------------------------------------------
# STAGE 1: Builder
# ------------------------------------------------------------------------------
FROM python:3.11-slim-bookworm AS builder

# Prevent python from writing pyc files and buffer stdin/out
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# Install OS compile-time dependencies required for C-extensions
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    python3-dev \
    && rm -rf /var/lib/apt/lists/*

# Create isolated virtual environment
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

WORKDIR /build

# Copy dependency specifications first to maximize Docker layer cache reuse
COPY requirements.txt pyproject.toml ./

# Install wheels into isolated virtual environment
RUN pip install --no-cache-dir --upgrade pip wheel && \
    pip install --no-cache-dir -r requirements.txt


# ------------------------------------------------------------------------------
# STAGE 2: Hardened Runtime
# ------------------------------------------------------------------------------
FROM python:3.11-slim-bookworm AS runner

# Runtime environment flags
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH"

# Install minimal dynamic runtime libraries (libpq for PostgreSQL wire protocol)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    && rm -rf /var/lib/apt/lists/*

# Create dedicated unprivileged system group and user (CIS Docker Benchmark compliant)
RUN groupadd -g 10001 appgroup && \
    useradd -u 10001 -g appgroup -s /sbin/nologin -d /app appuser

WORKDIR /app

# Copy compiled virtual environment from builder stage (Zero compiler toolchains in runner!)
COPY --from=builder /opt/venv /opt/venv

# Copy application source code with non-root ownership
COPY --chown=appuser:appgroup app /app/app
COPY --chown=appuser:appgroup alembic /app/alembic
COPY --chown=appuser:appgroup alembic.ini /app/alembic.ini

# Ensure correct permissions on /app directory
RUN chown -R appuser:appgroup /app

# Switch to unprivileged non-root user
USER appuser:appgroup

# Expose HTTP port
EXPOSE 8000

# Standard Docker Healthcheck pointing to Day 75 in-memory Liveness probe
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health/liveness')"

# Production execution entrypoint with bounded workers
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
