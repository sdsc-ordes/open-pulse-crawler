# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Follow relationships: `UserModel` now carries `followers` and `following` login lists, populated from the GitHub API (and cached) per crawled user. CSV export includes `follows` edges (`source` follows `target`) between users that are both present in the graph. Follow lists do not expand the crawl — they are recorded as edges only.
- Starred and watched repositories: `UserModel` gains `starred_repositories` and `watched_repositories` lists, sourced from `users/<login>/starred` and `users/<login>/subscriptions`. CSV export emits `starred` and `watching` edges between users and repos that are both present in the graph. Like follows, these do not expand the crawl.
- `TeamModel` for GitHub organization teams (`org/slug` full-name, members, repos, parent team, privacy) and a new `teams` collection on `GraphData`. When the auth token has access to an org's teams, teams are fetched live and emit `has_team` (org → team), `member_of` (user → team), `has_access` (team → repo), and `parent_of` (team → team) edges. Team data is fetched on the live API path only; orgs served from existing cache will not be re-checked for teams until the cache is invalidated.
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
- Optional gimie JSON-LD hybrid repository fetching (`gimie_repos` request flag) and `/api/v1/crawl/{job_id}/jsonld.zip` download endpoint.
- Docker Compose stack (`infra/docker-compose.yml`) for `api`, `gui`, and `nginx` services on a shared network with env-file configuration and per-service health checks.
- End-to-end Docker integration test script (`tests/test_integration.sh`) validating health, auth behavior, and GUI/API routing through Nginx.

### Changed

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

[Unreleased]: https://github.com/sdsc-ordes/open-pulse-crawler/compare/v0.1.0...HEAD
