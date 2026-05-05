# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- `--max-contributors` skip rule (CLI flag, REST API field on `POST /api/v1/crawl`, Streamlit GUI input under "Performance & filtering", and constructor argument on `GitHubCrawler`). When set, repos with more than N contributors stay in the graph but their contributor users are not queued for BFS expansion — owner / fork / dependency / dependent edges are unaffected. Useful for avoiding mega-projects (linux kernel, etc.) that would otherwise dominate the frontier.
- `RepoModel.contributor_count` and `RepoModel.skipped_high_contributors` fields. The total contributor count is captured the first time a repo is fetched (one cheap `per_page=1` request via `repo.get_contributors().totalCount`) and cached alongside the existing `repo:{full_name}` entry, so repeat crawls re-apply the skip rule with zero additional API calls.
- `GitHubClient.get_contributor_count(repo_full_name)` helper — cache-first lookup with a single live fallback on miss; write-throughs the count so the next call hits cache.
- Streamlit GUI brought to parity with the REST API: form now exposes `crawl_dependencies`, `crawl_dependents`, `min_stars`, `max_dependents`, `max_contributors`, and `batch_size` (under collapsible "Dependency crawling" and "Performance & filtering" expanders, with sensible defaults that don't surface the params in the request body unless changed). Results panel now shows live progress (current round, nodes processed, queue size, ETA) for active jobs, includes pause / resume / cancel / delete controls gated by current status, and offers a graph download button once the job is `completed`. API errors are surfaced with their `detail` message instead of a generic HTTP error string.
- MkDocs documentation site (Material theme) at `mkdocs.yml`, with a `docs` extra group in `pyproject.toml` (`mkdocs`, `mkdocs-material`, `pymdown-extensions`). Rendered Mermaid diagrams via `pymdownx.superfences`: an architecture diagram on the home page, a job-lifecycle state diagram and a request-flow sequence diagram in `docs/API.md`, and a Compose-stack flow diagram in `docs/DEPLOYMENT.md`. Build verified locally with `mkdocs build --strict`.
- `docs/index.md` landing page introducing the project and linking into the existing docs.
- GitHub Action `.github/workflows/docs.yaml` that builds the site on push / PR to `main` / `develop` (with `--strict`) and deploys to GitHub Pages from `main` via `actions/deploy-pages`. **Requires Pages source = "GitHub Actions"** in repo Settings → Pages.
- Job lifecycle endpoints in the REST API:
  - `GET /api/v1/jobs` — list every job in the in-memory registry, newest first, with optional `status_filter`.
  - `POST /api/v1/crawl/{job_id}/pause` — pause at the next BFS round boundary.
  - `POST /api/v1/crawl/{job_id}/resume` — resume a paused job.
  - `POST /api/v1/crawl/{job_id}/cancel` — cooperative cancel; partial graph is preserved.
  - `DELETE /api/v1/crawl/{job_id}` — drop a terminal job from the registry.
- Live progress fields on `GET /api/v1/crawl/{job_id}` for running jobs: `started_at`, `completed_at`, `current_round`, `nodes_processed`, `nodes_in_queue`, and a best-effort `estimated_completion_at`.
- `paused` and `cancelled` job-status values.
- Server-side gimie configuration via env vars: `GIMIE_ENABLED`, `GIMIE_API_BASE`, `GIMIE_STORE_JSONLD`, `GIMIE_SKIP_EXISTING_JSONLD`. When enabled, each crawled repo is enriched via a sibling git-metadata-extractor service and (optionally) JSON-LD payloads are persisted under `${OPC_DATA_DIR}/<job_id>/jsonld/`.
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
- Optional gimie JSON-LD hybrid repository fetching (initially as a `gimie_repos` request flag; superseded by env-var configuration — see `Added` above).
- Docker Compose stack (`infra/docker-compose.yml`) for `api`, `gui`, and `nginx` services on a shared network with env-file configuration and per-service health checks.
- End-to-end Docker integration test script (`tests/test_integration.sh`) validating health, auth behavior, and GUI/API routing through Nginx.

### Changed

- Fixed `infra/docker-compose.yml` nginx healthcheck: replaced `wget http://localhost/api/v1/health` with `wget http://127.0.0.1/api/v1/health`. Inside the Alpine container, `localhost` resolves to `::1` (IPv6) but `infra/nginx/nginx.conf` only declares `listen 80;` (IPv4-only), so the probe was getting "Connection refused" while external access worked fine. Stack went from `unhealthy` to healthy in 7s after recreate.
- Crawl export filenames: timestamp first, then kind — e.g. `YYYYMMDDHHMMSS.graph.json`, `YYYYMMDDHHMMSS.edges.csv`, `YYYYMMDDHHMMSS.nodes.csv`, `YYYYMMDDHHMMSS.graph.png`, directory `YYYYMMDDHHMMSS.clusters/`. Incremental round folders are `YYYYMMDDHHMMSS.round_NN/` with the same inner naming.
- Gimie JSON-LD: on success (HTTP 2xx) or when using an existing payload file with skip-existing, remove matching `jsonld_errors/<repo>.*.json` files for that repository.
- Gimie JSON-LD: log a short preview of the HTTP error response body when the gimie endpoint returns non-2xx (in addition to optional `jsonld_errors/` files).
- Gimie JSON-LD: `force_refresh=true` is always sent on HTTP fetches (not a CLI/API flag). `--gimie-skip-existing-jsonld` only checks for existing files under the crawl `jsonld/` output directory.
- Renamed `jsonld/*.json` export/processing filenames to include timestamp as `name.timestamp.json` (timestamp derived from crawl export time or filesystem metadata by the provided rename script).
- Expanded deployment docs in `docs/DEPLOYMENT.md` with Docker Compose setup, environment configuration, health verification, and integration test usage.
- Updated `README.md` with dedicated REST API and Docker/GUI quick-start sections and links to deployment/API docs.
- Completed API docs (`docs/API.md`) with reverse-proxy base URL notes and practical `curl` examples for crawl/status/graph flows.
- Extended `POST /api/v1/crawl` to accept CLI-aligned crawl controls: `crawl_dependencies`, `crawl_dependents`, `min_stars`, `max_dependents`, `batch_size`, and inline `epfl_entities`.
- Moved Docker infrastructure files into `infra/` and updated commands/scripts to use `docker compose -f infra/docker-compose.yml ...`.
- Updated compose services to pull the app container from `ghcr.io/sdsc-ordes/open-pulse-crawler:latest` by default (`OPC_IMAGE` override supported).
- Gimie hybrid extraction is now configured via environment variables (`GIMIE_ENABLED`, `GIMIE_API_BASE`, `GIMIE_STORE_JSONLD`, `GIMIE_SKIP_EXISTING_JSONLD`), not the per-request `gimie_repos` flag. Operators decide whether the gimie path is on; clients submitting crawls don't need to know.
- Cleaned up `docs/`: removed completion-report markdown (`*_COMPLETE`, `*_FIX_SUMMARY`, `*_IMPLEMENTATION`, `IMPROVEMENTS_SUMMARY`, `PLAN_*`, `QUICK_REFERENCE`, etc.) and the duplicate copies of files that already live under `docs/dev/dependency-graph/`. The remaining doc set is `API.md`, `DEPLOYMENT.md`, `CONCURRENCY.md`, `PROGRESS_TRACKING.md`, `TIMESTAMPS.md`, `VISUALIZATION.md`, plus `docs/dev/`. README's broken `RATE_LIMITING.md` link now points at `docs/CONCURRENCY.md`.
- Refreshed `docs/API.md` to match the current API surface (job list / pause / resume / cancel / delete, live progress fields with ETA, env-driven gimie config) and corrected the Dockerfile path in `docs/DEPLOYMENT.md` (`tools/image/Dockerfile`).

[Unreleased]: https://github.com/sdsc-ordes/open-pulse-crawler/compare/v0.1.0...HEAD
