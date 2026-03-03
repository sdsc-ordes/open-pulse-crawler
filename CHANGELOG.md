# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Nginx reverse-proxy container (`infra/nginx/Dockerfile` + `infra/nginx/nginx.conf`): routes `/api/*` to FastAPI (port 8000) and `/` to Streamlit (port 8501) with WebSocket upgrade support.
- Streamlit placeholder GUI (`src/open_pulse_crawler/gui.py`) with token input sidebar, crawl form (seeds + BFS rounds), and live job-status results area.
- `streamlit` and `httpx` added to project dependencies.
- Root `Dockerfile` — multi-stage build (uv builder + python:3.12-slim runtime), exposes port 8000, runs uvicorn as non-root user.
- `.dockerignore` to keep production images lean.
- Dockerfile structure and API entrypoint tests (`tests/test_dockerfile.py`).
- Deployment documentation (`docs/DEPLOYMENT.md`).
- `AGENTS.md` with contributor and AI-agent guidelines.
- `CHANGELOG.md` following Keep a Changelog format.
- Bearer-token auth module (`src/open_pulse_crawler/auth.py`) using `HTTPBearer` + `secrets.compare_digest` against `API_TOKEN` env var.
- Auth test suite (`tests/test_auth.py`).
- `API_TOKEN` variable added to `.env.dist`.
- FastAPI REST API (`src/open_pulse_crawler/api.py`) with endpoints:
  - `GET /api/v1/health` — public health check.
  - `POST /api/v1/crawl` — start a background crawl job (Bearer auth).
  - `GET /api/v1/crawl/{job_id}` — job status and summary.
  - `GET /api/v1/graph/{job_id}` — full graph data for completed jobs.
- `fastapi` and `uvicorn[standard]` added to project dependencies; `httpx` added to dev dependencies.
- API test suite (`tests/test_api.py`).
- API documentation (`docs/API.md`).
- Docker Compose stack (`infra/docker-compose.yml`) for `api`, `gui`, and `nginx` services on a shared network with env-file configuration and per-service health checks.
- End-to-end Docker integration test script (`tests/test_integration.sh`) validating health, auth behavior, and GUI/API routing through Nginx.

### Changed

- Expanded deployment docs in `docs/DEPLOYMENT.md` with Docker Compose setup, environment configuration, health verification, and integration test usage.
- Updated `README.md` with dedicated REST API and Docker/GUI quick-start sections and links to deployment/API docs.
- Completed API docs (`docs/API.md`) with reverse-proxy base URL notes and practical `curl` examples for crawl/status/graph flows.
- Extended `POST /api/v1/crawl` to accept CLI-aligned crawl controls: `crawl_dependencies`, `crawl_dependents`, `min_stars`, `max_dependents`, `batch_size`, and inline `epfl_entities`.
- Moved Docker infrastructure files into `infra/` and updated commands/scripts to use `docker compose -f infra/docker-compose.yml ...`.
- Updated compose services to pull the app container from `ghcr.io/sdsc-ordes/open-pulse-crawler:latest` by default (`OPC_IMAGE` override supported).

[Unreleased]: https://github.com/sdsc-ordes/open-pulse-crawler/compare/v0.1.0...HEAD
