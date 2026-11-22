# Multi-stage build for Engramory MCP gateway & Worker daemon
FROM ghcr.io/astral-sh/uv:0.9.0 AS uv_bin
FROM python:3.12-slim-bookworm AS builder

WORKDIR /build

COPY --from=uv_bin /uv /uvx /usr/local/bin/

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml uv.lock README.md ./
COPY src/ ./src/

RUN UV_PROJECT_ENVIRONMENT=/app/.venv uv sync --frozen --no-dev --no-editable

# Final runtime stage
FROM python:3.12-slim-bookworm

ARG USER_ID=1004
ARG GROUP_ID=1004

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    git \
    && rm -rf /var/lib/apt/lists/* && \
    groupadd -g ${GROUP_ID} engramory && \
    useradd -u ${USER_ID} -g engramory -m -s /bin/bash engramory

WORKDIR /app

COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /build/src /app/src

RUN chown -R engramory:engramory /app

USER engramory

ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    ENGRAMORY_PROFILE=dev \
    ENGRAMORY_REFLECT_INTERVAL_SEC=300

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import os, sys, tempfile, time; p = os.environ.get('ENGRAMORY_HEARTBEAT_PATH') or os.path.join(tempfile.gettempdir(), 'engramory-worker.heartbeat'); sys.exit(0 if os.path.exists(p) and (time.time() - os.path.getmtime(p)) < 180 else 1)"

ENTRYPOINT ["engramory", "worker", "start"]
