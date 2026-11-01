# Multi-stage build for Engramory Worker daemon (PLAN-005 Phase 5)
FROM python:3.12-slim-bookworm AS builder

WORKDIR /build

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY src/ ./src/

RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir --prefix=/install .

# Final runtime stage
FROM python:3.12-slim-bookworm

ARG USER_ID=1004
ARG GROUP_ID=1004

RUN groupadd -g ${GROUP_ID} engramory && \
    useradd -u ${USER_ID} -g engramory -m -s /bin/bash engramory

WORKDIR /app

COPY --from=builder /install /usr/local
COPY --from=builder /build/src /app/src

USER engramory

ENV PYTHONUNBUFFERED=1 \
    ENGRAMORY_PROFILE=dev \
    ENGRAMORY_REFLECT_INTERVAL_SEC=300

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD test $(find /tmp/engramory-worker.heartbeat -mmin -10 2>/dev/null) || exit 1

ENTRYPOINT ["engramory", "worker", "start"]
