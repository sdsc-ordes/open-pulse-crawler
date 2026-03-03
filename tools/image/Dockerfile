# ----- stage 1: build wheel -----
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src/ src/

RUN uv pip install --system --no-cache-dir build \
    && python -m build --wheel --outdir /app/dist

# ----- stage 2: runtime -----
FROM python:3.12-slim AS runtime

LABEL maintainer="Open Pulse Crawler contributors"

RUN groupadd --gid 1000 app \
    && useradd --uid 1000 --gid app --create-home app

WORKDIR /app

COPY --from=builder /app/dist/*.whl /tmp/

RUN pip install --no-cache-dir /tmp/*.whl && rm -rf /tmp/*.whl

USER app
EXPOSE 8000

ENTRYPOINT ["uvicorn", "open_pulse_crawler.api:app"]
CMD ["--host", "0.0.0.0", "--port", "8000"]
