# Production image for the API (FastAPI + the local embedding/reranking models).
# Built and started by docker-compose.prod.yml; see README "Deployment".
#
# Model weights (~4.5 GB) are NOT baked in: they download on the first start
# into the `models` volume (/models), so the image stays small, rebuilds after a
# code change are fast, and restarts reuse the weights.

# Bookworm pinned: its system package names (libglib2.0-0) are stable.
FROM python:3.12-slim-bookworm

# uv copied from its official image (pinned to the version used in development).
COPY --from=ghcr.io/astral-sh/uv:0.12.1 /uv /usr/local/bin/uv

# libgl1 + libglib2.0-0: OpenCV (used by Docling's OCR) needs them to import.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    # Install into /app/.venv and put it first on PATH, so `fastapi`, `alembic`
    # and `python` are the project's versions without `uv run`.
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH" \
    # Every model cache points into the /models volume.
    HF_HOME=/models/hf \
    FASTEMBED_CACHE_PATH=/models/fastembed \
    XDG_CACHE_HOME=/models/cache

WORKDIR /app

# Dependencies first, in their own layer: it is rebuilt only when the lockfile
# changes, not on every code change. --frozen = use uv.lock exactly as committed.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# Then the code. Only what the API needs at runtime (see .dockerignore).
COPY app ./app
COPY alembic ./alembic
COPY alembic.ini ./
COPY corpus/manifest.jsonl ./corpus/manifest.jsonl

# Run as an unprivileged user: if the app were ever compromised, the attacker
# would not be root inside the container. It owns the two writable folders;
# Docker copies this ownership into the named volumes when they are created.
RUN useradd --create-home --uid 1000 app \
    && mkdir -p /app/uploads /models \
    && chown -R app:app /app/uploads /models
USER app

EXPOSE 8000

# One worker: each worker process would load its own ~5 GB copy of the models.
# --root-path /api: Caddy serves the API under /api and strips that prefix, so
# FastAPI must know it to give Swagger the right /api/openapi.json URL.
# --proxy-headers + --forwarded-allow-ips *: trust Caddy's X-Forwarded-* headers
# (scheme, client IP). Safe because port 8000 is never published: only Caddy,
# on the private Docker network, can reach this container.
CMD ["fastapi", "run", "app/main.py", "--host", "0.0.0.0", "--port", "8000", \
     "--workers", "1", "--root-path", "/api", \
     "--proxy-headers", "--forwarded-allow-ips", "*"]
