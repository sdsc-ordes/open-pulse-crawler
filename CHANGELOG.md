# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- GitLab support across multiple instances: `gitlab.com`, `gitlab.epfl.ch`, `gitlab.ethz.ch`, `renkulab.io`.
- `PlatformAdapter` abstraction + `PlatformRegistry` for host-keyed dispatch.
- Concrete `GitHubAdapter` (wrapping the existing client) and `GitLabAdapter` (new, on `python-gitlab`).
- `subkind` discriminator on every node (`GitHubUser`, `GitHubOrganization`, `GitHubRepository`, `GitHubTeam`, `GitLabUser`, `GitLabGroup`, `GitLabProject`) with typed GitLab subclasses carrying platform-specific fields (e.g., `GitLabProjectModel.visibility`, `.namespace`, `GitLabGroupModel.parent`).
- `extras: dict[str, Any]` and `external_identifiers: list[ExternalIdentifier]` on every node — escape hatch for fields we don't model and slot for future cross-platform identity linking.
- `/api/v2` — unified shape including `subkind`. Endpoints: `health`, `platforms`, `crawl`, `graph/{job_id}`, `nodes` filtered listing.
- CLI: `--platforms`, `--default-host`, `--crawl-stars` flags on `crawl`.
- CLI: `crawler doctor` (`opc doctor`) reports enabled hosts and per-host token configuration.
- Host-keyed env vars: `CRAWLER_TOKEN_POOL__<HOST>`, `CRAWLER_TOKEN__<HOST>`; `CRAWLER_PLATFORMS` to enable instances.
- `python-gitlab>=4,<6` as a required dependency.

### Changed
- Snapshot schema bumped to 3; v2 state files are now refused on load with `IncompatibleStateError`.
- Cache layout: `cache/<host>/<sha256(uri)>.json`. Existing v2 flat-cache directories are not migrated — re-crawl required.
- Visualization color map keyed by `subkind`. GitHub subkinds keep the v2 cyan/gold/green palette so existing crawls render identically; GitLab subkinds use distinct purple/brown/pink colors.

### Deprecated
- `CRAWLER_GITHUB_TOKEN_POOL`, `CRAWLER_GITHUB_TOKEN`, `GITHUB_TOKEN`: still work for `github.com` in v3 with a one-shot deprecation warning. Removed in v4.
- `/api/v1`: github-only crawls keep working. Non-github seeds rejected with `400 {"error": "v1 supports github.com seeds only; use /api/v2"}`. Endpoint removed in v4.

### Added (v3.1 — Zenodo adapter)
- `ZenodoAdapter` and `ZenodoClient` under `src/open_pulse_crawler/platforms/zenodo/`.
- Three subkind models: `ZenodoUserModel`, `ZenodoCommunityModel`,
  `ZenodoRecordModel`. Records use the concept DOI as the primary
  identity; versions collapse into a `versions: list[dict]` field.
- DOI URL → canonical Zenodo URL rewriting in `node_id.py`
  (`is_zenodo_doi_url`, `rewrite_zenodo_doi_url`).
- Five new edge kinds: `in_community`, `uploaded_by`, `uploaded`,
  `contains`, and compound `related_to.<RelationType>` (`isSupplementTo`,
  `cites`, `isCitedBy`, …) for cross-platform discovery.
- CLI registers `ZenodoAdapter` for `zenodo.org`, `sandbox.zenodo.org`,
  and any `*.zenodo.org` host with or without tokens.
- `tools/scripts/fetch_public_projects.py` handles `zenodo.org` via
  `/api/records` keyset pagination.
- `POST /api/v2/crawl` OpenAPI examples: `zenodo_community_renku`,
  `zenodo_record_doi_url`, `zenodo_record_canonical`,
  `cross_platform_zenodo_github`.
- Integration test against live `zenodo.org`
  (`tests/integration/test_zenodo_dryrun.py`).
- `docs/ZENODO.md`.

### Out of scope (v3.1)
- Community member crawling (auth-gated on production Zenodo).
- Per-version record nodes (versions are intra-node metadata).
- Cross-platform identity resolution (handled by another tool).

### Added (v3.2 — Infoscience adapter)
- `InfoscienceAdapter` and `InfoscienceClient` under
  `src/open_pulse_crawler/platforms/infoscience/`. Crawls EPFL's
  Infoscience repository (DSpace 7.6.2 + DSpace-CRIS 2023.02.06).
- Three subkind models: `InfosciencePerson`, `InfoscienceOrgUnit`,
  `InfoscienceItem`. Items carry `resource_type` distinguishing
  publications from datasets/software/etc.
- Seven new edge kinds: `authored_by`, `affiliated_with`,
  `related_to.<RelationType>`, `authored`, `member_of`,
  `has_publication`, `parent_of`.
- Shared `platforms/datacite.py` helper for `arxiv`/`orcid`/`pmid`/
  `pmcid`/`swh`/`doi`/`url` URL synthesis (lifted from
  `ZenodoAdapter._synthesize_target_url`; both adapters now share it).
- HTTP 429 retry-after handling in `InfoscienceClient` (one automatic
  retry honoring `Retry-After` header, capped at 60s; second 429 raises).
- CRIS-CamelCase relation-type normalization (`dc.relation.isversionof`
  → `related_to.isVersionOf` for cross-platform consistency with Zenodo).
- UUID → handle resolution cache on the adapter (`_uuid_to_handle`)
  saves round-trips when the same Person co-authors multiple items.
- CLI registers `InfoscienceAdapter` for `infoscience.epfl.ch` and
  any `*.infoscience.epfl.ch` host with or without tokens.
- `tools/scripts/fetch_public_projects.py` handles
  `infoscience.epfl.ch` via DSpace's `/server/api/discover/search/objects`
  keyset pagination.
- `POST /api/v2/crawl` OpenAPI examples: `infoscience_publication_handle`,
  `infoscience_person_authored_chain`.
- Integration test against live `infoscience.epfl.ch`
  (`tests/integration/test_infoscience_dryrun.py`).
- `docs/INFOSCIENCE.md`.

### Fixed (v3.2)
- `InfoscienceClient.get_item_by_handle` now uses the documented DSpace
  PID resolver (`/server/api/pid/find?id=hdl:<handle>`) rather than the
  undocumented `/server/api/handle/<handle>` path.

### Refactored (v3.2)
- `ZenodoAdapter._synthesize_target_url` lifted to module-level
  `synthesize_target_url` in `platforms/datacite.py`. Zenodo behavior
  unchanged; tests for the synthesizer moved to `tests/platforms/test_datacite.py`.

### Out of scope (v3.2)
- DSpace community/collection structural layer (OrgUnit covers the
  canonical EPFL hierarchy).
- DSpace-CRIS Project entities (grants, funding).
- Cross-platform identity resolution (still downstream of this tool).
- Other Swiss DSpace instances (UZH ZORA, UNIBE BORIS) — refactor
  `InfoscienceAdapter` into a parameterized `DSpaceAdapter` when a
  second instance lands.

### Added (v3.3 — DataCite Commons adapter)
- `DataCiteAdapter` and `DataCiteHTTPClient` under
  `src/open_pulse_crawler/platforms/datacite_adapter/`. Crawls DataCite
  Commons — DOI-identified works (any DataCite repository), ROR
  organizations, ORCID researchers, and DataCite-registered repositories.
- Four subkind models: `DataCiteWork`, `DataCiteOrganization`,
  `DataCitePerson`, `DataCiteClient`.
- Six new edge kinds: `authored_by`, `affiliated_with`,
  `related_to.<RelationType>`, `published_by` (from work), `has_publication`
  (from organization), `authored` (from person). `DataCiteClient` is
  passive — no edges emitted.
- DOI prefix routing table (`_DOI_PREFIX_REWRITERS` in
  `platforms/datacite.py`): Zenodo-prefix DOIs are rewritten to their
  canonical `zenodo.org` URLs at seed-time, so the BFS routes them to
  the Zenodo adapter (no duplicate node).
- `PlatformRegistry.register_hosts(hosts, adapter)` for multi-host
  registration. The DataCite adapter is registered against five hosts
  (`doi.org`, `ror.org`, `orcid.org`, `api.datacite.org`,
  `commons.datacite.org`); user-facing platform key is `datacite.org`.
- `NodeKind.USER` and `NodeKind.ORG` added (alongside existing
  `USER_OR_ORG`) — used by adapters whose URL shapes are discriminated
  (DataCite ORCID vs ROR vs commons-repository).
- HTTP 429 retry-after handling in `DataCiteHTTPClient` (one automatic
  retry honoring `Retry-After` header, capped at 60s; second 429 raises).
- DataCite client metadata enrichment via `/clients/<id>`: `clientType`,
  `domains` (for cross-host routing hints), `re3data_doi` (cross-registry
  anchor), `doi_prefixes` (via `/clients/<id>/relationships/prefixes`).
- CLI registers `DataCiteAdapter` against `datacite.org` (anonymous + tokens).
- `POST /api/v2/crawl` OpenAPI examples: `datacite_work_by_doi`,
  `datacite_org_by_ror_epfl`, `datacite_person_by_orcid`.
- Integration test against live `api.datacite.org`
  (`tests/integration/test_datacite_dryrun.py`).
- `docs/DATACITE.md`.

### Refactored (v3.3)
- `node_id.rewrite_zenodo_doi_url` and `node_id.is_zenodo_doi_url`
  removed. Replaced by the generalized `rewrite_doi_url` /
  `is_owned_doi_url` in `platforms/datacite.py`, table-driven via
  `_DOI_PREFIX_REWRITERS`. Behavior is unchanged for Zenodo.

### Out of scope (v3.3)
- Crossref-issued DOIs (Nature, ACM, IEEE). A future `CrossrefAdapter`
  could share the DOI host via the prefix routing table.
- ROR / ORCID secondary API enrichment. Cross-platform identity
  resolution stays downstream.
- Active `DataCiteClient` expansion (walking all DOIs published by a
  client). Could be added behind a `--crawl-client-works` flag.
- `tools/scripts/fetch_public_projects.py` extension for DataCite —
  no natural "browse all DOIs" use case yet.

### Added (v3.4 — HuggingFace adapter)
- `HuggingFaceAdapter` and `HuggingFaceHTTPClient` under
  `src/open_pulse_crawler/platforms/huggingface/`. Crawls HuggingFace —
  users, organizations, models, datasets, spaces, papers, and collections.
- Five subkind models: `HuggingFaceUser`, `HuggingFaceOrg`,
  `HuggingFaceRepo` (models/datasets/spaces unified via `repo_type:
  Literal["model","dataset","space"]`), `HuggingFacePaper`,
  `HuggingFaceCollection`.
- Twelve new edge kinds: `owns`, `member_of`, `owned_by`, `uses_model`,
  `related_to.IsIdenticalTo` (paper → arxiv), `related_to.IsSupplementedBy`
  (paper → github), `references_model`, `references_dataset`,
  `references_space`, `contains`.
- Cross-platform paper bridge: a HuggingFace paper's arxiv ID emits the
  same canonical `arxiv.org/abs/<id>` URL that Zenodo, Infoscience, and
  DataCite records produce when they reference the same paper. The
  shared `synthesize_target_url` helper handles the URL synthesis with
  no HF-specific routing code.
- USER_OR_ORG disambiguation pattern (matches GitHub): `huggingface.co/<name>`
  classifies as `USER_OR_ORG`; `fetch` probes `/api/users/<name>/overview`
  first and falls through to `/api/organizations/<name>/overview` on 404.
- Reserved first-segment words (`datasets`, `spaces`, `papers`,
  `collections`, plus HF top-level routes `blog`, `docs`, `tasks`,
  `learn`, `pricing`, `enterprise`, `inference-endpoints`) are
  recognized by `classify` and `normalize_uri` to prevent the
  bare-`<owner>/<name>` model regex from misclaiming them.
- HTTP 429 retry-after handling in `HuggingFaceHTTPClient` (one
  automatic retry honoring `Retry-After`, capped at 60s).
- Link-header cursor pagination on list endpoints
  (`/api/models?author=<x>`, `/api/datasets?author=<x>`, `/api/spaces?author=<x>`).
- CLI registers `HuggingFaceAdapter` against `huggingface.co`
  (anonymous-friendly; tokens optional).
- `POST /api/v2/crawl` OpenAPI examples: `huggingface_paper_llama2`,
  `huggingface_model_llama`, `huggingface_user_karpathy`.
- Integration test against live `huggingface.co`
  (`tests/integration/test_huggingface_dryrun.py`) — verifies the
  cross-platform paper bridge end-to-end with the Llama 2 paper seed.
- `docs/HUGGINGFACE.md`.

### Out of scope (v3.4)
- `has_member` (Org → User) edges — auth-gated endpoint, same as
  Infoscience.
- Paper authors → ORCID linkage — paper authors are bare `{name}`
  strings without ORCID. Cross-platform identity stays downstream.
- Model/dataset card README parsing (`cardData.tags` arxiv extraction).
- Discussion / community / dataset-viewer / model-leaderboard data.
- Legacy arxiv IDs (`papers/cond-mat/0303517` form).
- `tools/scripts/fetch_public_projects.py` extension for HF — no
  natural "browse all HF entities" use case.

## [2.0.0] — 2026-05-27

**Breaking release.** Headlines:

* Node identifiers are now canonical public URLs (e.g. `https://github.com/torvalds`). Graph dict keys, CSV `id`/`source`/`target` columns, and the API response shape all change. See [`docs/MIGRATION_v1_to_v2.md`](docs/MIGRATION_v1_to_v2.md) for the field-by-field diff and the operator checklist.
* GitHub-token env vars renamed to `CRAWLER_GITHUB_TOKEN` / `CRAWLER_GITHUB_TOKEN_POOL`. Legacy `GITHUB_TOKEN` still read with a one-shot deprecation warning.
* Snapshot + state schema version bumped to `2`. Snapshots written under 1.x are refused on load — operators must re-crawl.
* GraphQL-backed crawl endpoint, multi-token rotation (proactive + reactive), cache TTL, resume-from-state, partial-graph recovery, issue/PR activity edges, team modeling, gimie hybrid mode, and richer documentation accumulate from the v1.x line.

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
  - **Token rotation:** a comma-separated `GITHUB_TOKEN` rotates across all tokens, matching the REST client (previously the GraphQL client accepted the list but only ever used the first one). Rotation is both *proactive* — after a query reports `remaining: 0` the client switches before the next query — and *reactive* — when an already-exhausted token rejects a query outright the client rotates and retries that query. A rejection is detected by HTTP 403/429 or a rate-limit GraphQL error; the error is matched on its **message** ("rate limit"), not its `type`, because GitHub's spelling varies (`RATE_LIMITED` vs `RATE_LIMIT`) and some rate-limit errors carry no `type` at all. Only once every token has been tried and is exhausted does it sleep until the window resets. `token_switches` / `rate_limit_waits` are reported in `get_stats()`.
  - **Pagination:** `User.starredRepositories`, `User.repositories`, `User.followers`, `User.following`, and `User.watching` now paginate via cursors up to `max_per_list` items (default 1000), so GraphQL mode returns the same full lists as REST — closes the parity gap against power users (e.g. 243 starred repos on `cmdoret` were truncated to 100 in the initial implementation). Only `User.organizations` stays at first-100 (it is scope-gated behind `read:org`, and >100 org memberships effectively never happens).
- Issue and PR activity edges, opt-in:
  - `RepoModel` gains `issue_authors`, `pr_authors`, `commenters`, and `pr_reviewers` lists.
  - New CLI flags `--crawl-issues` and `--crawl-prs` (off by default; matches the existing `--crawl-dependencies` / `--crawl-dependents` pattern).
  - `--issue-max` / `--pr-max` caps (default 100) limit how many of the most-recent issues/PRs are scanned per repo, mirroring the existing 10-contributor cap.
  - CSV export emits `issue_author`, `pr_author`, `commented_on`, and `pr_reviewer` edges between users present in the graph and the repo.
  - `commenters` covers conversation comments on both issues and PRs (both come from the issues API); `pr_reviewers` covers formal PR reviews only.
  - The same parameters are exposed on `POST /api/v1/crawl` (`crawl_issues`, `crawl_prs`, `issue_max`, `pr_max`).
- `TeamModel` for GitHub organization teams (`org/slug` full-name, members, repos, parent team, privacy) and a new `teams` collection on `GraphData`. When the auth token has access to an org's teams, teams are fetched live and emit `has_team` (org → team), `has_access` (team → repo), and `parent_of` (team → team) edges. Team data is fetched on the live API path only; orgs served from existing cache will not be re-checked for teams until the cache is invalidated.
- `OPC_CACHE_DIR` environment variable for the API response cache directory, and a `--no-cache` CLI flag to disable caching. See **Changed** for the new default behavior.
- Cache entry expiry (TTL). A cached GitHub API response older than `OPC_CACHE_TTL_DAYS` (default **30**) is now treated as a miss and refetched, so the cache self-refreshes instead of serving indefinitely-stale data (previously entries never expired). The age is read from the cache file's mtime; `OPC_CACHE_TTL_DAYS=0` disables expiry. Both the REST and GraphQL backends apply it via `APICache(..., ttl_seconds=resolve_cache_ttl())`. Caching behavior is now documented end-to-end in [docs/DEPLOYMENT.md → Caching](docs/DEPLOYMENT.md).
- `--max-contributors` optional per-repo contributor cap (CLI flag, REST API field on `POST /api/v1/crawl`, Streamlit GUI input under "Performance & filtering", and constructor argument on `GitHubCrawler`). When set it is a **take-up-to-N limit**: at most N contributors are recorded and queued per repo, and a repo with more is truncated to its top N — it still contributes, it is never skipped to zero. **Unset (the default) there is no cap** — every contributor the GitHub API returns is recorded as an edge and queued (GitHub itself caps the contributors endpoint at ~500 for very large repos). Like every cap in the project, `max_contributors` means "take up to N", never "skip to 0".
- `RepoModel.contributor_count` field — the repo's total contributor count, recorded as metadata. Captured the first time a repo is fetched (one cheap `per_page=1` request via `repo.get_contributors().totalCount`) and cached.
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

- A crawl could crash with `PermissionError` when the cache directory wasn't writable. `APICache` did `mkdir(parents=True)` in its constructor and let the error propagate; with the repo-relative default (`data/open-pulse-crawler/cache`) this killed the first crawl inside a container, whose CWD (`/app`) is root-owned. `APICache` now disables itself (logs a warning, `get`/`set` become no-ops) instead of raising — a crawl never fails over the cache, which is only an optimization. The REST and GraphQL endpoints were affected identically (both call `resolve_cache_dir()`).
- Partial crawl results were unrecoverable. `record.graph` was assigned only on the success path, so a FAILED job — or any job whose container restarted — lost every completed round (the graph lived solely in a local variable that was garbage-collected). The job record now references the live `GraphData` object before the crawl starts, and per-round disk snapshots make the partial graph durable across process restarts.

### Changed

- Hardened the Docker image build. The publish workflow (`publish_image_in_GHCR.yaml`) now has a `workflow_dispatch` trigger, so a tag can be rebuilt/republished on demand from the Actions tab without pushing a dummy commit. The `tools/image/Dockerfile` builder no longer mounts a persistent uv cache on the project-install step — since the package version isn't bumped per commit, a cached build could bake stale source into the image; the project is built fresh every time (dependency downloads in the earlier step keep their cache).
- Enriched the auto-generated API docs at `/api/v1/docs` (Swagger UI): an app-level overview with the typical request flow, endpoints grouped under `Health` / `Crawl` / `Job lifecycle` / `Graph` tags, a `summary` per endpoint, field-level descriptions and example payloads on every request/response model, documented error responses (401/403/404/409/422), described path/query parameters, and four ready-to-send request-body examples on the crawl endpoints that prefill the "Try it out" form.
- API response caching is now **on by default**. Resolution order: explicit `--cache-dir` → `OPC_CACHE_DIR` env var → a caller-supplied default → the repo-relative `data/open-pulse-crawler/cache`. The CLI uses the repo-relative default (`data/` is git-ignored); the REST/GraphQL API defaults to `${OPC_DATA_DIR}/cache` instead, since that path is writable inside the container while the process CWD is not. An empty `OPC_CACHE_DIR` (or the CLI `--no-cache` flag) disables caching.
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
- Renamed the GitHub token environment variables to disambiguate single-token and pool deployments:
  - `CRAWLER_GITHUB_TOKEN` — a single GitHub PAT.
  - `CRAWLER_GITHUB_TOKEN_POOL` — comma-separated list of PATs for rotation. Wins over `CRAWLER_GITHUB_TOKEN` when both are set.
  - The legacy `GITHUB_TOKEN` is still read as a deprecated fallback (logs a one-shot warning); migrate to the new names. Resolution lives in `open_pulse_crawler.token_env.resolve_github_tokens()` and is shared by the CLI, the REST/GraphQL API workers, and the tool scripts under `tools/scripts/`. Docs, `.env.dist`, the runtime Dockerfile, and the integration test bootstrap are updated to use the new names.
- **BREAKING — URL-keyed graph nodes.** Every node in the graph is now keyed by its canonical public URL (e.g. `https://github.com/torvalds`, `https://github.com/torvalds/linux`, `https://github.com/orgs/acme/teams/core`) — the foundation for multi-platform crawling (GitHub today, GitLab next).
  - `GraphData.users` / `.orgs` / `.repos` / `.teams` dicts are now keyed by canonical URL instead of `login` / `full_name`. Each model gains a `url: str` field (the canonical identifier) and a `platform: str = "github"` field; `login` / `full_name` / `owner` / `slug` are retained for display + API calls.
  - The GitHub clients' public methods (`get_user`, `get_organization`, `get_repository`, `get_contributor_count` on both the REST `GitHubClient` and the GraphQL `GitHubGraphQLClient`) now take a canonical URL. Cache keys are URL-based.
  - CSV edge export (`source`/`target`) and node export (`id` column) emit canonical URLs. The JSON export's node dict keys are canonical URLs.
  - Snapshot + state schema version bumped to `2`. Snapshots and crawler `state.json` written under earlier versions are refused on read — operators must re-crawl (no migration script ships).
  - Seed input is unchanged: the CLI and `POST /api/v1/crawl` still accept any of `torvalds` / `owner/repo` / `https://github.com/...` and normalize to canonical URL form internally. URL normalization (lowercase host, no trailing slash, path case preserved) lives in the new `open_pulse_crawler.node_id` module.
  - **Multi-platform groundwork only — Phase 1 is GitHub-only.** A future PR introduces the per-host `PlatformAdapter` dispatch and the first non-GitHub adapter.

### Removed

- `member_of` edges (user → org and user → team) are no longer emitted in the CSV export. Org and team member lists are not a complete public signal — non-publicized org members are hidden from external tokens, and team membership requires org-level access — so they were dropped in favor of richer public signals (follows, stars, contributions). The underlying `OrgModel.members` and `TeamModel.members` lists are still populated in the JSON dump.

[Unreleased]: https://github.com/sdsc-ordes/open-pulse-crawler/compare/v2.0.0...HEAD
[2.0.0]: https://github.com/sdsc-ordes/open-pulse-crawler/compare/v1.0.0...v2.0.0
