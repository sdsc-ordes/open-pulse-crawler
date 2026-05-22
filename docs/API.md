# Open Pulse Crawler — REST API

Base path: `/api/v1`

Interactive Swagger docs are available at `/api/v1/docs` when the server is running.
When using Docker Compose with Nginx, the same API is reachable at
`http://localhost/api/v1` (or `http://localhost:${OPC_PORT}/api/v1` if overridden).

## Authentication

All endpoints except `/api/v1/health` require a Bearer token:

```
Authorization: Bearer <token>
```

The token is validated against the `API_TOKEN` environment variable on the server
using a constant-time comparison (`secrets.compare_digest`).

| Scenario                       | Status    | Detail                                    |
| ------------------------------ | --------- | ----------------------------------------- |
| Missing / non-Bearer header    | `401/403` | Not authenticated                         |
| Invalid token                  | `401`     | Invalid or missing API token              |
| `API_TOKEN` env var not set    | `503`     | API_TOKEN is not configured on the server |

The authentication logic lives in `src/open_pulse_crawler/auth.py` and is applied
as a FastAPI dependency on every protected endpoint.

## Endpoints

### `GET /api/v1/health`

Public health check.

**Response** `200`

```json
{
  "status": "ok",
  "version": "0.1.0"
}
```

---

### `POST /api/v1/crawl`

Start a new crawl job. The crawl runs asynchronously in the background.

**Request body**

| Field        | Type       | Required | Default | Description                           |
| ------------ | ---------- | -------- | ------- | ------------------------------------- |
| `seeds`      | `string[]` | yes      | —       | Seed nodes (users, orgs, or repos)    |
| `max_rounds` | `int`      | no       | `2`     | BFS rounds (1–10)                     |
| `crawl_dependencies` | `bool` | no | `false` | Crawl repository dependencies (downstream) |
| `crawl_dependents` | `bool` | no | `false` | Crawl repository dependents (upstream) |
| `min_stars` | `int` | no | `0` | Minimum stars for dependency/dependent filtering |
| `max_dependents` | `int \| null` | no | `null` | Maximum number of dependents to fetch (>=1) |
| `batch_size` | `int \| null` | no | `null` | Number of nodes processed concurrently (>=1) |
| `epfl_entities` | `string[]` | no | `[]` | Entity names (users/orgs) tagged as EPFL |
| `crawl_issues` | `bool` | no | `false` | Fetch issue authors and conversation commenters per repo (opt-in; expensive on busy repos) |
| `crawl_prs` | `bool` | no | `false` | Fetch PR authors, conversation commenters, and reviewers per repo (opt-in; expensive on busy repos) |
| `issue_max` | `int` | no | `100` | Max issues scanned per repo when `crawl_issues` is set (most recent first) |
| `pr_max` | `int` | no | `100` | Max PRs scanned per repo when `crawl_prs` is set (most recent first) |
| `gimie_repos` | `bool` | no | `false` | Populate repository nodes from gimie JSON-LD (user/org still from GitHub API). |
| `gimie_api_base` | `string` | no | `http://host.docker.internal:1234` | Base URL for the gimie JSON-LD API. |
| `gimie_store_jsonld` | `bool` | no | `false` | Store raw gimie JSON-LD payloads on disk under the job directory. |
| `gimie_skip_existing_jsonld` | `bool` | no | `false` | If storing, skip HTTP when a payload file already exists under the job `jsonld/` directory (on-disk only). Actual gimie requests always include `force_refresh=true`. |
| `gimie_archive_on_download` | `bool` | no | `false` | If true, try to create `jsonld.zip` after crawl completion (otherwise it is created on demand). |

```json
{
  "seeds": ["torvalds", "sdsc-ordes/gimie"],
  "max_rounds": 3,
  "crawl_dependents": true,
  "min_stars": 10,
  "max_dependents": 100,
  "epfl_entities": ["epfl", "dslab-epfl"],
  "gimie_repos": true,
  "gimie_store_jsonld": true
}
```

**Response** `202 Accepted`

```json
{
  "job_id": "d290f1ee-6c54-4b01-90e6-d701748f0851",
  "status": "pending"
}
```

---

### `POST /api/v1/crawl/graphql`

Start a crawl using the **GraphQL-backed** client. Accepts the **same request
body** as `POST /api/v1/crawl` and produces the same graph; it is plugged into
the same BFS engine, so rounds, expansion, and edge export are identical.

GraphQL collapses what the REST path does in dozens of requests into one query
per entity — e.g. fetching a repo's issue/PR activity costs ~1 GraphQL point
versus ~80 REST requests.

GraphQL covers: user/org/repo metadata, follows, starred, watching, teams, and
issue/PR activity. Three things still fall back to REST automatically: the top
contributors list (no public GraphQL endpoint), SBOM `crawl_dependencies`, and
the "Used by" `crawl_dependents` graph.

**Token scopes:** GraphQL requires `read:org` for org-level fields
(login/name/members/teams). The user query self-heals when `read:org` is
missing — it drops the `organizations` field and re-fetches that list via REST —
but `GET /api/v1/crawl/graphql` crawls seeded on an organization need a token
with `read:org`. The `gimie_*` options are not supported on this endpoint;
use `POST /api/v1/crawl` for gimie hybrid mode.

**Response** `202 Accepted` — identical shape to `POST /api/v1/crawl`.

---

### `GET /api/v1/crawl/{job_id}`

Get status and summary counts of a crawl job.

**Response** `200`

```json
{
  "job_id": "d290f1ee-6c54-4b01-90e6-d701748f0851",
  "status": "completed",
  "detail": null,
  "users": 42,
  "orgs": 5,
  "repos": 87,
  "rounds_completed": 2
}
```

Possible `status` values: `pending`, `running`, `completed`, `failed`, `stopped`.
`rounds_completed` is the number of BFS rounds finished so far (useful for
monitoring progress and for partial reads).

Returns `404` if the job ID is unknown.

---

### `POST /api/v1/crawl/{job_id}/stop`

Request a **cooperative stop** of a running crawl. Returns immediately; the
crawl halts at the next BFS round boundary — the in-flight round drains first,
so the graph stays round-consistent — and the job's status then settles to
`stopped`. A stopped job's state is persisted and can be continued with
`POST .../resume`.

A stop that arrives after the crawl already finished all its work is a no-op:
the job is reported `completed`, not `stopped`.

**Response** `200`

```json
{
  "job_id": "d290f1ee-6c54-4b01-90e6-d701748f0851",
  "status": "running",
  "detail": "Stop requested; the crawl will halt after the current round drains."
}
```

Returns `404` if the job ID is unknown, or `409 Conflict` if the job is not in a
stoppable state (`pending`/`running`).

---

### `POST /api/v1/crawl/{job_id}/resume`

Resume a `stopped` or `failed` crawl from its persisted BFS state — the saved
queue, visited set, and graph — instead of re-crawling from the seeds. The crawl
is re-dispatched on whichever client (REST or GraphQL) the original job used.

Resume works even when the in-memory job record was lost (container restart), as
long as the job's `state.json` and `request.json` are still on disk under
`OPC_DATA_DIR/{job_id}/` — see [Persistence](#persistence).

**Response** `202 Accepted`

```json
{
  "job_id": "d290f1ee-6c54-4b01-90e6-d701748f0851",
  "status": "pending",
  "detail": "Resuming crawl from saved state."
}
```

Returns `404` if no persisted request exists for the job, or `409 Conflict` if
there is no saved crawler state, or if the job is already `pending`/`running`.

---

### `GET /api/v1/graph/{job_id}`

Return graph data for a crawl job.

By default only a **completed** job is served. Pass `?partial=true` to also read
a partial graph — from a still-`running`/`stopped`/`failed` job, or, when the
in-memory job record is gone (container restart), from the last per-round disk
snapshot keyed by `job_id`.

**Query parameters**

| Param     | Type   | Default | Description                                              |
| --------- | ------ | ------- | -------------------------------------------------------- |
| `partial` | `bool` | `false` | Allow reading a non-completed / snapshot-recovered graph |

**Response** `200`

```json
{
  "job_id": "d290f1ee-6c54-4b01-90e6-d701748f0851",
  "graph": {
    "users": { "torvalds": { "login": "torvalds", "..." : "..." } },
    "orgs": {},
    "repos": {}
  },
  "partial": false,
  "status": "completed",
  "rounds_completed": 2
}
```

`partial` is `true` when the returned graph is not a final completed result.

Without `?partial=true`: returns `404` if the job ID is unknown, or `409 Conflict`
if the job has not completed. With `?partial=true`: returns the latest snapshot,
or `404`/`409` if no snapshot exists yet.

---

### `GET /api/v1/crawl/{job_id}/jsonld.zip`

Download the stored gimie JSON-LD payloads (`application/zip`) for a completed crawl job.

Returns `404` if the job does not exist, is not completed, or `gimie_store_jsonld` was not enabled for that job.

## Persistence

The job store is in-memory, so a job's status is lost on container restart.
To make graph data durable, every crawl writes to disk under
`OPC_DATA_DIR/{job_id}/` (default `OPC_DATA_DIR` is `/tmp/open-pulse-crawler`):

| File                  | Written            | Purpose                                          |
| --------------------- | ------------------ | ------------------------------------------------ |
| `graph.snapshot.json` | after every round  | Partial-graph reads via `GET /graph?partial=true` |
| `state.json`          | after every round  | Full BFS state for `POST .../resume`             |
| `request.json`        | at job creation    | Original crawl config, so resume rebuilds it     |

**Deployment note:** mount `OPC_DATA_DIR` to a persistent volume in production.
Without a mount, snapshots and resumable state do not survive a container
restart or OOM-kill, and `POST .../resume` / `GET /graph?partial=true` recovery
will not work across restarts.

## Quick curl examples

With Docker Compose + Nginx:

```bash
export API_BASE="http://localhost/api/v1"
export API_TOKEN="my-secret-api-token"
```

Running FastAPI directly (no Nginx):

```bash
export API_BASE="http://localhost:8000/api/v1"
export API_TOKEN="my-secret-api-token"
```

Start crawl:

```bash
curl -X POST "$API_BASE/crawl" \
  -H "Authorization: Bearer $API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"seeds":["torvalds"],"max_rounds":2}'
```

Check status:

```bash
curl "$API_BASE/crawl/<job_id>" \
  -H "Authorization: Bearer $API_TOKEN"
```

Fetch graph:

```bash
curl "$API_BASE/graph/<job_id>" \
  -H "Authorization: Bearer $API_TOKEN"
```

Start a GraphQL-backed crawl (same body as `/crawl`):

```bash
curl -X POST "$API_BASE/crawl/graphql" \
  -H "Authorization: Bearer $API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"seeds":["sdsc-ordes/gimie"],"max_rounds":2,"crawl_issues":true,"crawl_prs":true}'
```

Stop a running crawl, then read its partial graph:

```bash
curl -X POST "$API_BASE/crawl/<job_id>/stop" \
  -H "Authorization: Bearer $API_TOKEN"

curl "$API_BASE/graph/<job_id>?partial=true" \
  -H "Authorization: Bearer $API_TOKEN"
```

Resume a stopped crawl:

```bash
curl -X POST "$API_BASE/crawl/<job_id>/resume" \
  -H "Authorization: Bearer $API_TOKEN"
```

## Running the Server

```bash
export API_TOKEN="my-secret"
export GITHUB_TOKEN="ghp_..."
export OPC_DATA_DIR="/var/lib/crawler/jobs"        # optional; defaults to /tmp/open-pulse-crawler
export OPC_CACHE_DIR="/var/lib/crawler/cache"      # optional; defaults to data/open-pulse-crawler/cache
uvicorn open_pulse_crawler.api:app --host 0.0.0.0 --port 8000
```

`OPC_DATA_DIR` is where per-job snapshots and resumable state are written — see
[Persistence](#persistence). Point it at a mounted volume so partial results and
resume survive container restarts.

`OPC_CACHE_DIR` is where GitHub API responses are cached between crawls; it
defaults to `data/open-pulse-crawler/cache`. Set it to an empty string to
disable caching.
