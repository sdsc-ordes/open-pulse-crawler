# syntax=docker/dockerfile:1.7
#
# Multi-stage runtime image for the Open Pulse Crawler API.
#
# Mirrors the toolchain choice of .devcontainer/Dockerfile (uv + Python 3.12)
# so dev and prod environments share the same resolver and lockfile semantics.
#
# Build:
#   docker build -f tools/image/Dockerfile -t open-pulse-crawler:local .
#
# Run:
#   docker run --rm -p 8000:8000 \
#     -e API_TOKEN=...  -e GITHUB_TOKEN=...  open-pulse-crawler:local

# ----- stage 1: build venv with uv ------------------------------------------
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv

WORKDIR /app

# Step 1a: install only locked dependencies, no project source. This layer is
# reused as long as pyproject.toml + uv.lock don't change, which is the
# common case during code-only iteration.
COPY pyproject.toml uv.lock README.md ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project --no-editable

# Step 1b: copy source and install the project itself into the venv. The
# --no-editable flag bakes the package files into site-packages so the
# runtime image only needs /app/.venv (no src/ copy required).
COPY src/ src/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable

# ----- stage 2: minimal runtime ---------------------------------------------
FROM python:3.12-slim AS runtime

LABEL maintainer="Open Pulse Crawler contributors" \
      org.opencontainers.image.source="https://github.com/sdsc-ordes/open-pulse-crawler" \
      org.opencontainers.image.description="Open Pulse Crawler API (FastAPI)"

RUN groupadd --gid 1000 app \
 && useradd  --uid 1000 --gid app --create-home app

WORKDIR /app

# The venv is fully self-contained — copy it over and stop. No pip, no uv,
# no system-level install needed in the runtime layer.
COPY --from=builder --chown=app:app /app/.venv /app/.venv

ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

USER app
EXPOSE 8000

# Healthcheck mirrors the open-pulse compose probe so a `docker run` outside
# of compose still self-reports liveness.
HEALTHCHECK --interval=15s --timeout=5s --start-period=10s --retries=5 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/v1/health')" || exit 1

ENTRYPOINT ["uvicorn", "open_pulse_crawler.api:app"]
CMD ["--host", "0.0.0.0", "--port", "8000"]
