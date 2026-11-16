# syntax=docker/dockerfile:1

# ---- build stage: dependencies + application code -------------------------
FROM python:3.12-slim AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

COPY --from=ghcr.io/astral-sh/uv:0.13.0 /uv /uvx /bin/

WORKDIR /app

# Dependency layer: invalidated only when the lockfile changes. The cache mount
# keeps the uv download cache out of the image layer.
COPY apps/server/pyproject.toml apps/server/uv.lock apps/server/README.md ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

# Explicit runtime files only: never copy tests, local data/ or .env.
COPY apps/server/app ./app
COPY apps/server/alembic ./alembic
COPY apps/server/alembic.ini ./

# ---- runtime stage: no compiler/uv toolchain, non-root --------------------
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOST=0.0.0.0 \
    PORT=8000 \
    DATA_DIR=/data \
    PATH="/app/.venv/bin:$PATH"

RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p /data \
    && chown appuser:appuser /data

WORKDIR /app
COPY --from=builder --chown=appuser:appuser /app /app

USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=3)"

# One worker is a correctness requirement for the single-writer SQLite store
# and per-scene asyncio locks (PRD §5.5, D12); app/__main__.py also guards it.
CMD ["sh", "-c", "alembic upgrade head && python -m app"]