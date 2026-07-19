# syntax=docker/dockerfile:1

FROM python:3.12-slim AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

RUN python -m pip install --no-cache-dir uv==0.9.3

WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project

COPY src ./src
RUN uv sync --frozen --no-dev --no-editable


FROM python:3.12-slim AS runtime

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    FOUNDERLOOKUP_DATA_DIR=/app/data

RUN apt-get update \
    && apt-get install --yes --no-install-recommends gosu \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system founderlookup \
    && useradd --system --gid founderlookup --home-dir /app founderlookup \
    && mkdir -p /app/data \
    && chown -R founderlookup:founderlookup /app

WORKDIR /app
COPY --from=builder --chown=founderlookup:founderlookup /app/.venv /app/.venv

EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=5 \
    CMD ["python", "-c", "import os, urllib.request; port = os.getenv('PORT', '8000'); urllib.request.urlopen(f'http://127.0.0.1:{port}/health', timeout=2).read()"]

# Railway volumes replace the image directory at runtime and initially mount as root.
# Fix only the mount point, then drop privileges before starting the application.
CMD ["sh", "-c", "chown founderlookup:founderlookup /app/data && exec gosu founderlookup uvicorn founderlookup.main:app --host 0.0.0.0 --port \"${PORT:-8000}\""]
