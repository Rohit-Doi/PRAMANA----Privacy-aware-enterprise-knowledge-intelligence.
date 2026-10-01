# syntax=docker/dockerfile:1.7
# PRAMĀṆA backend: one image for the API, the ingestion worker and the one-off jobs.
# Build from the repository root:   docker build -f backend/Dockerfile -t pramana-backend .

# ── Build stage: resolve and install dependencies with uv ───────────────────
FROM python:3.12-slim-bookworm AS builder
COPY --from=ghcr.io/astral-sh/uv:0.10.11 /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never
WORKDIR /app/backend

# Dependencies first: this layer is rebuilt only when the lock file changes.
COPY backend/pyproject.toml backend/uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-group dev

COPY backend/src ./src
COPY backend/alembic.ini ./
COPY backend/migrations ./migrations
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-group dev

# ── Runtime stage ───────────────────────────────────────────────────────────
FROM python:3.12-slim-bookworm AS runtime
RUN groupadd --system --gid 10001 pramana \
    && useradd --system --uid 10001 --gid pramana --home-dir /app --shell /usr/sbin/nologin pramana

ENV PRAMANA_ROOT=/app \
    PATH=/app/backend/.venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app/backend
COPY --from=builder --chown=pramana:pramana /app/backend /app/backend
COPY --chown=pramana:pramana policies /app/policies
COPY --chown=pramana:pramana scripts /app/scripts
COPY --chown=pramana:pramana eval /app/eval

# Model files and uploaded documents live on volumes, not in the image.
RUN mkdir -p /app/.cache /app/data && chown -R pramana:pramana /app/.cache /app/data
VOLUME ["/app/.cache", "/app/data"]

USER pramana
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=5s --start-period=120s --retries=5 \
    CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=3).status == 200 else 1)"]

# The API by default. The worker overrides the command with:  python -m pramana.worker
CMD ["uvicorn", "pramana.api.app:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
