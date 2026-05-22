# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Resume-from-state: `POST /api/v1/crawl/{job_id}/resume` — in addition to lifting a pause — continues a `cancelled`/`failed` job from its persisted BFS state (queue + visited set + graph) rather than re-crawling from the seeds. Works even after the in-memory job record is lost (container restart), as long as the job's `state.json` + `request.json` are on disk. The background crawl wires the crawler's `state_file` (written per round) and persists the original request so the resume rebuilds the exact config — REST or GraphQL.
- Partial-graph recovery for crawl jobs:
  - The background crawl now writes the graph to disk after every BFS round (and once more on the terminal transition) as `{OPC_DATA_DIR}/{job_id}/graph.snapshot.json`, written atomically and tagged with `status` + `rounds_completed`.
  - `GET /api/v1/graph/{job_id}` accepts `?partial=true`. Without it the strict contract is unchanged (only a COMPLETED in-memory job is served). With it, a still-RUNNING or FAILED job's partial graph is returned from the last round snapshot — and a job whose in-memory record was lost (container restart/OOM) is recoverable by `job_id` from the same snapshot.
  - `GraphResponse` gained `partial`, `status`, and `rounds_completed`; `CrawlResultResponse` gained `rounds_completed`.
- Follow relationships: `UserModel` now carries `followers` and `following` login lists, populated from the GitHub API (and cached) per crawled user. CSV export includes `follows` edges (`source` follows `target`) between users that are both present in the graph. Follow lists do not expand the crawl — they are recorded as edges only.
- Starred and watched repositories: `UserModel` gains `starred_repositories` and `watched_repositories` lists, sourced from `users/<login>/starred` and `users/<login>/subscriptions`. CSV export emits `starred` and `watching` edges between users and repos that are both present in the graph. Like follows, these do not expand the crawl.
- GraphQL-backed crawl endpoint:
  - New `POST /api/v1/crawl/graphql` accepts the same `CrawlRequest` body as `/api/v1/crawl`.
  - New `GitHubGraphQLClient` (`graphql_client.py`) is duck-type compatible with the REST `GitHubClient`; it serves user / org / repo data via `api.github.com/graphql` and is plugged into the existing `GitHubCrawler` so BFS rounds, expansion, and edge export work unchanged.
  - One GraphQL query covers everything `--crawl-issues` + `--crawl-prs` previously fetched via dozens of REST calls — measured cost on `sdsc-ordes/gimie` (issue-max 25, pr-max 25): 1 GraphQL point + 1 REST contributors call (1.5s) vs 80 REST calls (97s) for the same data.
  - Crawler's cached-path branches now also materialize teams and issue/PR fields from dict payloads, so any client returning that shape (file cache, GraphQL) populates the same edges.
  - REST is still used for: top contributors (no public GraphQL equivalent), SBOM dependencies, and the "Used by" dependents graph — the latter two only fire when their existing flags are set.
  - Gimie hybrid mode is not wired into the GraphQL endpoint; use `/api/v1/crawl` for that.
  - **Token scopes:** GraphQL requires `read:org` for any org-level field (login/name/members/teams) and `User.organizations` — REST returns the same public data with a less strict scope check. The user query now self-heals when the token lacks `read:org`: it falls back to a no-organizations query and re-fetches `orgs` via REST (`/users/{login}/orgs`, no scope needed) so the field still populates. Org-level queries (`get_organization`) still require `read:org`. Errors are surfaced at WARNING.
  - **Pagination:** `User.starredRepositories` and `User.repositories` now paginate via cursors up to `max_per_list` items (default 1000) — closes the parity gap observed against power users (e.g. 243 starred repos on `cmdoret` were truncated to 100 in the initial implementation). Other connections (`followers`, `following`, `watching`, `organizations`) stay at first-100 — most users are well under that cap.
- Issue and PR activity edges, opt-in:
  - `RepoModel` gains `issue_authors`, `pr_authors`, `commenters`, and `pr_reviewers` lists.
  - New CLI flags `--crawl-issues` and `--crawl-prs` (off by default; matches the existing `--crawl-dependencies` / `--crawl-dependents` pattern).
  - `--issue-max` / `--pr-max` caps (default 100) limit how many of the most-recent issues/PRs are scanned per repo, mirroring the existing 10-contributor cap.
  - CSV export emits `issue_author`, `pr_author`, `commented_on`, and `pr_reviewer` edges between users present in the graph and the repo.
  - `commenters` covers conversation comments on both issues and PRs (both come from the issues API); `pr_reviewers` covers formal PR reviews only.
  - The same parameters are exposed on `POST /api/v1/crawl` (`crawl_issues`, `crawl_prs`, `issue_max`, `pr_max`).
- `TeamModel` for GitHub organization teams (`org/slug` full-name, members, repos, parent team, privacy) and a new `teams` collection on `GraphData`. When the auth token has access to an org's teams, teams are fetched live and emit `has_team` (org → team), `has_access` (team → repo), and `parent_of` (team → team) edges. Team data is fetched on the live API path only; orgs served from existing cache will not be re-checked for teams until the cache is invalidated.
- `OPC_CACHE_DIR` environment variable for the API response cache directory, and a `--no-cache` CLI flag to disable caching. See **Changed** for the new default behavior.
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

### Fixed

- Partial crawl results were unrecoverable. `record.graph` was assigned only on the success path, so a FAILED job — or any job whose container restarted — lost every completed round (the graph lived solely in a local variable that was garbage-collected). The job record now references the live `GraphData` object before the crawl starts, and per-round disk snapshots make the partial graph durable across process restarts.

### Changed

- Enriched the auto-generated API docs at `/api/v1/docs` (Swagger UI): an app-level overview with the typical request flow, endpoints grouped under `Health` / `Crawl` / `Job lifecycle` / `Graph` tags, a `summary` per endpoint, field-level descriptions and example payloads on every request/response model, documented error responses (401/403/404/409/422), described path/query parameters, and four ready-to-send request-body examples on the crawl endpoints that prefill the "Try it out" form.
- API response caching is now **on by default**, stored at `data/open-pulse-crawler/cache`. Previously the CLI cached only when `--cache-dir` was passed, and the REST/GraphQL API never cached. Resolution order: explicit `--cache-dir` → `OPC_CACHE_DIR` env var → the default path; an empty `OPC_CACHE_DIR` (or the CLI `--no-cache` flag) disables caching. The `data/` directory is already git-ignored.
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

### Removed

- `member_of` edges (user → org and user → team) are no longer emitted in the CSV export. Org and team member lists are not a complete public signal — non-publicized org members are hidden from external tokens, and team membership requires org-level access — so they were dropped in favor of richer public signals (follows, stars, contributions). The underlying `OrgModel.members` and `TeamModel.members` lists are still populated in the JSON dump.

[Unreleased]: https://github.com/sdsc-ordes/open-pulse-crawler/compare/v0.1.0...HEAD
