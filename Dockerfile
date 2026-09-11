# syntax=docker/dockerfile:1

# ---------------------------------------------------------------------------
# Builder: install dependencies and the project with uv into a virtualenv
# ---------------------------------------------------------------------------
FROM python:3.12-slim-trixie@sha256:2fe5997d249a808b8eeea52c58a1dbffbba28754dc11699ef5c029f2d818ce79 AS builder

ENV UV_PYTHON_DOWNLOADS=0 \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_NO_DEV=1

# Pin the uv version and digest for reproducible builds
COPY --from=ghcr.io/astral-sh/uv:0.11.32@sha256:df4cae8f3a96d175e2e5f992e597550000edbe78fdc2594d5cd8de1a217f504c /uv /uvx /bin/

WORKDIR /app

# Install dependencies first (only lock + manifest are needed), leveraging
# BuildKit cache mounts so rebuilds are fast.
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked --no-install-project --no-editable

# Copy only what the runtime needs; `.git` is deliberately never copied, so
# it cannot leak into any image layer or BuildKit cache layer.
COPY pyproject.toml uv.lock README.md alembic.ini ./
COPY src ./src
COPY web ./web
COPY alembic ./alembic
COPY scripts ./scripts

# Bake the exact commit into /app/GIT_SHA so /version can report it without
# relying on build args (Dokploy builds don't receive dynamic ones). The
# whole build context is bind-mounted read-only for this single instruction
# only, so it never becomes a layer; the script reads `.git` from there if
# present (CI, Dokploy and local checkouts all provide it) and otherwise
# falls back to the GIT_SHA build arg (or "unknown").
ARG GIT_SHA=unknown
RUN --mount=type=bind,source=.,target=/ctx,ro \
    GIT_DIR=/ctx/.git python3 scripts/bake-git-sha.py > /app/GIT_SHA

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-editable

# ---------------------------------------------------------------------------
# Runtime: slim image without uv, tests or dev tooling
# ---------------------------------------------------------------------------
FROM python:3.12-slim-trixie@sha256:2fe5997d249a808b8eeea52c58a1dbffbba28754dc11699ef5c029f2d818ce79 AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/app/.venv/bin:$PATH" \
    HOST=0.0.0.0 \
    PORT=8000 \
    QRGEN_WEB_DIR=/app/web \
    QRGEN_REQUIRE_GIT_SHA=true

# Metadata for the image (set GIT_SHA at build time, e.g. in CI)
ARG GIT_SHA=unknown
ARG BUILD_DATE=unknown
# Also exposed at runtime so /version (and the deploy workflow) can verify
# the exact commit serving traffic.
ENV QRGEN_GIT_SHA=${GIT_SHA:-unknown} \
    QRGEN_BUILD_DATE=${BUILD_DATE:-unknown}
LABEL org.opencontainers.image.title="QR Studio" \
      org.opencontainers.image.description="Personalizable QR code generator (CLI + web UI)" \
      org.opencontainers.image.version="0.3.0" \
      org.opencontainers.image.revision="${GIT_SHA}" \
      org.opencontainers.image.created="${BUILD_DATE}"

WORKDIR /app

# Non-root user per best practice (runs with least privilege)
RUN adduser \
    --disabled-password \
    --gecos "" \
    --home /nonexistent \
    --shell /sbin/nologin \
    --no-create-home \
    --uid 10001 \
    appuser

COPY --from=builder --chown=appuser:appuser /app /app
RUN chmod +x /app/scripts/start.sh

USER appuser

EXPOSE 8000

# Liveness probe against the lightweight /healthz endpoint
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=3)"]

# Waits for the database, applies migrations, then serves the app.
CMD ["/app/scripts/start.sh"]
