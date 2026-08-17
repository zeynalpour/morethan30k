# syntax=docker/dockerfile:1
# --------------------------------------------------------------------------
# Production image for the TME gateway.
# Multi-stage: a builder resolves the locked dependency set with uv into a
# self-contained virtualenv; the final image copies only that venv + the app,
# so no build tooling (uv, compilers) ships to production.
# --------------------------------------------------------------------------

# --- Stage 1: build the venv from the frozen lockfile ---------------------
FROM python:3.12-slim AS builder

# Pinnable uv release (bump deliberately). Provides /bin/uv.
COPY --from=ghcr.io/astral-sh/uv:0.12 /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# Install dependencies first (cached layer) from the lockfile only.
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
# --frozen: fail if uv.lock is out of date. --no-dev: skip ruff/pytest/etc.
RUN uv sync --frozen --no-dev

# Migration assets needed at container start.
COPY alembic.ini ./
COPY migrations ./migrations

# --- Stage 2: minimal runtime -------------------------------------------
FROM python:3.12-slim

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# curl is used by the deploy health-check; run as a non-root user.
RUN apt-get update \
    && apt-get install --no-install-recommends -y curl \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 appuser

COPY --from=builder --chown=appuser:appuser /app /app

USER appuser
EXPOSE 8000

# Apply migrations, then serve. --proxy-headers because TME sits behind the
# host's existing reverse proxy. Single-replica assumption: migrations run on
# start; if you scale replicas, move migrations to a one-shot job instead.
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn tme.main:app --host 0.0.0.0 --port 8000 --proxy-headers --forwarded-allow-ips=*"]
