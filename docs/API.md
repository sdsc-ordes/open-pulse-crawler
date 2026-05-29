# Open Pulse Crawler — REST API

The server exposes two API versions side-by-side:

* **`/api/v2`** — current, multi-platform (GitHub + GitLab). New
  integrations should target this version.
* **`/api/v1`** — legacy, **GitHub-only** in v3 and **scheduled for
  removal in v4**. Non-`github.com` seeds are rejected with
  `400 {"error": "v1 supports github.com seeds only; use /api/v2"}`.

Interactive Swagger docs at `/api/v2/docs` (and `/api/v1/docs`) and the raw OpenAPI
documents at `/api/v2/openapi.json` / `/api/v1/openapi.json` when the server is running.
Behind Nginx the API is reachable at `http://localhost/api/v2` (or
`http://localhost:${OPC_PORT}/api/v2` if overridden); running uvicorn directly puts
it on `http://localhost:8000/api/v2`.

## Authentication

Every endpoint except `GET /api/v1/health` requires a Bearer token:

```
Authorization: Bearer <token>
```

The token is validated against the `API_TOKEN` environment variable on the server using
a constant-time comparison (`secrets.compare_digest`). The auth dependency lives in
`src/open_pulse_crawler/auth.py`.

| Scenario                       | Status    | Detail                                    |
| ------------------------------ | --------- | ----------------------------------------- |
| Missing / non-Bearer header    | `401/403` | Not authenticated                         |
| Invalid token                  | `401`     | Invalid or missing API token              |
| `API_TOKEN` env var not set    | `503`     | `API_TOKEN` is not configured on the server |

## Job lifecycle

```mermaid
stateDiagram-v2
    [*] --> pending: POST /crawl
    pending --> running: background task picks up
    running --> paused: POST /crawl/{id}/pause
    paused --> running: POST /crawl/{id}/resume
    running --> cancelled: POST /crawl/{id}/cancel<br/>(loop boundary)
    paused --> cancelled: POST /crawl/{id}/cancel<br/>(pause lifted)
    running --> completed: BFS finishes
    running --> failed: exception
    completed --> [*]: DELETE /crawl/{id}
    cancelled --> [*]: DELETE /crawl/{id}
    failed --> [*]: DELETE /crawl/{id}
```

`pending` flips to `running` as soon as the background task picks up the job. Cancellation
is cooperative — the BFS loop checks the cancel flag at round boundaries, so the partial
graph collected so far is preserved.

## Request flow

```mermaid
sequenceDiagram
    autonumber
    actor C as Client
    participant API as FastAPI
    participant BG as Background task
    participant GH as GitHub API
    participant G as git-metadata-extractor<br/>(optional)

    C->>API: POST /api/v1/crawl<br/>(Bearer + body)
    API->>BG: schedule _run_crawl(seeds, …)
    API-->>C: 202 {job_id, "pending"}

    BG->>BG: status = running

    loop For each BFS round
        BG->>GH: fetch users / orgs / repos
        GH-->>BG: graph data
        opt GIMIE_ENABLED=true
            BG->>G: POST /v1/extract per repo
            G-->>BG: JSON-LD
        end
        C->>API: GET /crawl/{job_id}
        API->>BG: read live progress
        API-->>C: progress + ETA
    end

    BG->>BG: status = completed
    C->>API: GET /graph/{job_id}
    API-->>C: 200 {graph}
```

## Endpoints

### `GET /api/v1/health` — public

```json
{
  "status": "ok",
  "version": "0.1.0"
}
```

### `POST /api/v1/crawl` — start a crawl

Starts a background BFS crawl and returns immediately with a job ID.

**Request body**

| Field                | Type          | Required | Default | Description                                |
| -------------------- | ------------- | -------- | ------- | ------------------------------------------ |
| `seeds`              | `string[]`    | yes      | —       | Seed nodes (users, orgs, or repos)         |
| `max_rounds`         | `int`         | no       | `2`     | BFS rounds (1–10)                          |
| `crawl_dependencies` | `bool`        | no       | `false` | Crawl downstream dependencies (SBOM)       |
| `crawl_dependents`   | `bool`        | no       | `false` | Crawl upstream dependents ("Used by")      |
| `min_stars`          | `int`         | no       | `0`     | Min stars for dep/dependent filtering      |
| `max_dependents`     | `int \| null` | no       | `null`  | Max dependents to fetch per repo (≥1)      |
| `max_contributors`   | `int \| null` | no       | `null`  | Optional per-repo contributor cap — take up to N (≥1), never skip; `null` = no cap — see below |
| `batch_size`         | `int \| null` | no       | `null`  | Concurrent nodes per round (≥1)            |

```json
{
  "seeds": ["torvalds", "sdsc-ordes/gimie"],
  "max_rounds": 3,
  "crawl_dependents": true,
  "min_stars": 10,
  "max_dependents": 100,
  "max_contributors": 200
}
```

#### `max_contributors`

An **optional** per-repo contributor limit. When set, at most N contributors
are recorded and queued per repo, and a repo with more is **truncated to its
top N — it still contributes, it is never skipped**. Owner / fork / dependency
/ dependent edges are unaffected.

Omitting the field (the default) means **no cap**: every contributor the GitHub
API returns is recorded as a `CONTRIBUTES_TO` edge and queued for crawling.
GitHub itself caps the contributors endpoint at ~500 for very large repos. The
repo's total `contributor_count` is always recorded as metadata.

Like every cap in this API, `max_contributors` means *take up to N* — never
*skip to 0*.

**Response** `202 Accepted`

```json
{
  "job_id": "d290f1ee-6c54-4b01-90e6-d701748f0851",
  "status": "pending"
}
```

> Note: gimie hybrid extraction (JSON-LD enrichment) is configured server-side via
> `GIMIE_*` environment variables, not per-request — see
> [`docs/DEPLOYMENT.md`](./DEPLOYMENT.md).

### `POST /api/v1/crawl/graphql` — start a GraphQL-backed crawl

Accepts the **same request body** as `POST /api/v1/crawl` and produces the same graph,
but fetches user / org / repo data via GitHub's GraphQL API — roughly one query per
entity instead of dozens of REST calls. Contributors, SBOM dependencies, and the
"Used by" dependents graph still fall back to REST. Org-level fields require a token
with `read:org` scope. Gimie hybrid mode is not available on this endpoint.

**Response** `202 Accepted` — identical shape to `POST /api/v1/crawl`.

### `GET /api/v1/crawl/{job_id}` — status, progress, ETA

Returns status, summary counts, and (for running jobs) live BFS progress.

**Response** `200`

```json
{
  "job_id": "d290f1ee-6c54-4b01-90e6-d701748f0851",
  "status": "running",
  "detail": null,
  "users": 12,
  "orgs": 3,
  "repos": 87,
  "started_at": "2026-05-05T08:30:15Z",
  "completed_at": null,
  "current_round": 2,
  "nodes_processed": 156,
  "nodes_in_queue": 234,
  "estimated_completion_at": "2026-05-05T08:31:42Z"
}
```

| Field                     | Populated when                                  |
| ------------------------- | ----------------------------------------------- |
| `started_at`              | the job has started running                     |
| `completed_at`            | the job has reached a terminal state            |
| `current_round`           | the job is `running`                            |
| `nodes_processed`         | the job is `running`                            |
| `nodes_in_queue`          | the job is `running`                            |
| `estimated_completion_at` | running, ≥1 node processed, queue non-empty     |

The ETA is a best-effort linear extrapolation from the current node-processing rate.
It drifts mid-BFS because the queue grows as the crawl expands; treat it as a hint, not
a guarantee.

`status` values: `pending`, `running`, `paused`, `completed`, `cancelled`, `failed`.
Returns `404` if the job ID is unknown.

### `GET /api/v1/jobs` — list all jobs

Returns every job currently in the in-memory registry, newest first
(by `completed_at`, then `started_at`).

**Query**

| Param           | Type        | Description                                          |
| --------------- | ----------- | ---------------------------------------------------- |
| `status_filter` | `JobStatus` | Narrow to one status (e.g. `?status_filter=completed`) |

**Response** `200`

```json
{
  "jobs": [
    {
      "job_id": "d290f1ee-6c54-4b01-90e6-d701748f0851",
      "status": "completed",
      "started_at": "2026-05-05T08:30:15Z",
      "completed_at": "2026-05-05T08:31:38Z",
      "users": 42,
      "orgs": 5,
      "repos": 87,
      "detail": null
    }
  ],
  "total": 1
}
```

### `GET /api/v1/graph/{job_id}` — fetch the graph

Graph data for a crawl job. By default only a **completed** job is served; pass
`?partial=true` to also read a partial graph from a running / cancelled / failed job,
or to recover a job by ID after its in-memory record was lost (container restart) from
the last per-round disk snapshot.

**Query parameters**

| Param     | Type   | Default | Description                                              |
| --------- | ------ | ------- | -------------------------------------------------------- |
| `partial` | `bool` | `false` | Allow reading a non-completed / snapshot-recovered graph |

**Response** `200`

```json
{
  "job_id": "d290f1ee-6c54-4b01-90e6-d701748f0851",
  "graph": {
    "schema_version": 2,
    "users": {
      "https://github.com/torvalds": {
        "url": "https://github.com/torvalds",
        "platform": "github",
        "login": "torvalds",
        "...": "..."
      }
    },
    "orgs": {},
    "repos": {},
    "teams": {}
  },
  "partial": false,
  "status": "completed",
  "rounds_completed": 2
}
```

Without `?partial=true`: `404` if the job ID is unknown, `409 Conflict` if the job has
not completed. With `?partial=true`: returns the latest round snapshot, or `404`/`409`
if no snapshot exists yet.

### Lifecycle controls

Pause, resume, and cancel are cooperative — they set flags on the live crawler instance
which the BFS loop checks at round boundaries. In-flight network calls finish first, so
the graph stays consistent (partial but not torn).

#### `POST /api/v1/crawl/{job_id}/pause`

Pauses at the next round boundary. Status flips to `paused` immediately; the loop sleeps
in 1-second ticks until `resume` or `cancel`.

`409` if the job isn't `running` or already `paused`.

#### `POST /api/v1/crawl/{job_id}/resume`

Lifts a previous pause. Also resumes a `cancelled` or `failed` job: it continues from
the persisted BFS state (queue + visited set + graph) instead of re-crawling from the
seeds, and works even after the in-memory job record was lost — as long as the job's
`state.json` + `request.json` are still on disk under `OPC_DATA_DIR/{job_id}/`.

`409` if the job is `running`/`pending`, or if there is no resumable state.

#### `POST /api/v1/crawl/{job_id}/cancel`

Asks the BFS loop to stop at the next round boundary. The final status becomes
`cancelled` once the loop exits. The graph collected up to that point is preserved —
read it with `GET /api/v1/graph/{job_id}?partial=true`, or continue the crawl with
`POST /api/v1/crawl/{job_id}/resume`.

`409` if the job is already in a terminal state.

#### `DELETE /api/v1/crawl/{job_id}`

Drops a terminal job from the in-memory registry — useful for clearing the listing in
long-lived deployments.

`409` if the job is still `running` or `paused` (cancel it first).

## `/api/v2` — multi-platform endpoints

Same auth model as v1 (Bearer `API_TOKEN`). Same job lifecycle. The shape
differs in that every node now carries a `subkind` discriminator
(`GitHubUser`, `GitHubOrganization`, `GitHubRepository`, `GitHubTeam`,
`GitLabUser`, `GitLabGroup`, `GitLabProject`) and the crawl endpoint
dispatches by host instead of assuming GitHub.

### `GET /api/v2/health` — public

```json
{
  "status": "ok",
  "version": "3.0.0",
  "api_version": "v2"
}
```

### `GET /api/v2/platforms`

Lists the platforms enabled on the server (driven by `CRAWLER_PLATFORMS`)
and reports per-host token configuration. Useful for clients to discover
where they can submit seeds before calling `POST /crawl`.

**Response** `200`

```json
{
  "platforms": [
    {"host": "github.com",     "tokens": 1, "ok": true},
    {"host": "zenodo.org",     "tokens": 0, "ok": true},
    {"host": "gitlab.epfl.ch", "tokens": 0, "ok": false}
  ]
}
```

Each row is `{host, tokens, ok}`:

- `host` — the configured platform host (a `CRAWLER_PLATFORMS` entry).
- `tokens` — number of tokens resolved for the host.
- `ok` — whether the host has at least one token. **This only checks token
  presence; it does not verify the token works** — use `crawler doctor` for
  an end-to-end probe. `ok` can be `true` with `tokens: 0` for
  anonymous-friendly hosts (Zenodo, Infoscience, DataCite, HuggingFace,
  GitLab), which crawl without a token; `crawler doctor` distinguishes the
  three states explicitly (OK / ANONYMOUS / MISSING).

The same data is available on the CLI via `opc doctor [--json]`.

### `POST /api/v2/crawl` — start a multi-platform crawl

Accepts seeds from any enabled host. The dispatcher looks at each seed's
host, routes it to the matching adapter (`GitHubAdapter` /
`GitLabAdapter`), and merges the per-host graphs in the response.

**Request body**

| Field                | Type          | Required | Default | Description                                       |
| -------------------- | ------------- | -------- | ------- | ------------------------------------------------- |
| `seeds`              | `string[]`    | yes      | —       | Seed URLs on any enabled host (or short forms)    |
| `platforms`          | `string[]`    | no       | all enabled | Restrict the crawl to a subset of enabled hosts |
| `default_host`       | `string`      | no       | `github.com` | Host assumed for short-form seeds (`owner/repo`) |
| `max_rounds`         | `int`         | no       | `2`     | BFS rounds (1–10)                                 |
| `crawl_stars`        | `bool`        | no       | `false` | Emit `starred` edges (GitHub + GitLab ≥13.5)      |
| `crawl_dependencies` | `bool`        | no       | `false` | GitHub-only; ignored for GitLab seeds             |
| `crawl_dependents`   | `bool`        | no       | `false` | GitHub-only; ignored for GitLab seeds             |
| `crawl_issues`       | `bool`        | no       | `false` | Issue authors / commenters                        |
| `crawl_prs`          | `bool`        | no       | `false` | PR authors / reviewers / commenters               |
| `min_stars`          | `int`         | no       | `0`     | Star filter on dep/dependent expansion            |
| `max_dependents`     | `int \| null` | no       | `null`  | Max dependents per repo (GitHub)                  |
| `max_contributors`   | `int \| null` | no       | `null`  | Per-repo / per-project contributor cap            |
| `batch_size`         | `int \| null` | no       | `null`  | Concurrent nodes per round                        |

```json
{
  "seeds": [
    "https://github.com/sdsc-ordes/open-pulse-crawler",
    "https://gitlab.com/gitlab-org/gitlab-foss",
    "https://gitlab.epfl.ch/some-group/some-project"
  ],
  "platforms": ["github.com", "gitlab.com", "gitlab.epfl.ch"],
  "max_rounds": 2,
  "crawl_stars": true
}
```

**Response** `202 Accepted` — identical shape to `/api/v1/crawl`:

```json
{
  "job_id": "d290f1ee-6c54-4b01-90e6-d701748f0851",
  "status": "pending"
}
```

`400` if any seed targets a host that is not enabled in `CRAWLER_PLATFORMS`.

### `GET /api/v2/graph/{job_id}` — fetch the graph

Same shape as `/api/v1/graph/{job_id}` plus every node carrying a
`subkind` discriminator and platform-specific fields. GitLab subkinds
add `visibility`, `namespace`, `parent` (groups), and other GitLab-only
fields; GitHub subkinds are unchanged from v1.

```json
{
  "job_id": "d290f1ee-…",
  "graph": {
    "schema_version": 3,
    "users": {
      "https://github.com/torvalds": {
        "subkind": "GitHubUser",
        "url": "https://github.com/torvalds",
        "platform": "github",
        "login": "torvalds",
        "contributors": [],
        "followers": ["caviri"],
        "extras": {},
        "external_identifiers": []
      }
    },
    "orgs": {
      "https://gitlab.com/gitlab-org": {
        "subkind": "GitLabGroup",
        "url": "https://gitlab.com/gitlab-org",
        "platform": "gitlab",
        "path": "gitlab-org",
        "visibility": "public",
        "parent": null,
        "members": ["someuser"],
        "extras": {},
        "external_identifiers": []
      }
    },
    "repos": {},
    "teams": {}
  },
  "partial": false,
  "status": "completed",
  "rounds_completed": 2
}
```

The graph keeps the `users` / `orgs` / `repos` / `teams` dicts (it is **not**
flattened into a single `nodes` dict — that is what `GET /api/v2/nodes`
returns). Each dict is keyed by the node's canonical URL, and every node
carries a `subkind` discriminator plus platform-specific fields.

> **⚠️ Edge-list fields are bare shorthand, not URLs.** Node *keys* are
> canonical URLs, but the per-node edge-list fields — `contributors`,
> `members`, `followers`, `following`, `starred_repositories`,
> `authored_repositories`, `forked_repositories`, … — contain **bare
> shorthand** (a login like `"caviri"`, or an `"owner/repo"` string), **not**
> URLs. This is intentional: the internal model keeps the compact form, and
> only the CSV / JSON-LD exporters (`export_to_csv` / JSON-LD) normalize
> endpoints to canonical URLs at write time. **The REST `/graph` response
> does NOT normalize them.**
>
> To join an edge endpoint back to a node key, prepend the owning node's
> host: a `followers` entry `"caviri"` on a node whose `url` is
> `https://github.com/marftn` resolves to `https://github.com/caviri`; the
> same shorthand on a `gitlab.epfl.ch` node resolves to
> `https://gitlab.epfl.ch/caviri`. Use the owning node's `url` host — do
> **not** assume `github.com`. If you want pre-joined URL→URL edges, use the
> CSV edges export or the JSON-LD output instead.

### `GET /api/v2/nodes` — filtered listing

Lightweight node listing useful for pagination / UI dropdowns when the
full graph is too large to load.

**Query parameters**

| Param      | Type     | Description                                                    |
| ---------- | -------- | -------------------------------------------------------------- |
| `subkind`  | `string` | Filter by subkind (e.g. `GitLabProject`)                       |
| `platform` | `string` | Filter by platform kind (`github` / `gitlab`)                  |
| `instance` | `string` | Filter by host (`github.com`, `gitlab.com`, `gitlab.epfl.ch`)  |
| `limit`    | `int`    | Max nodes returned (default 100)                               |
| `cursor`   | `string` | Opaque continuation token from a previous response             |

**Response** `200`

```json
{
  "nodes": [
    {"subkind": "GitLabProject", "url": "https://gitlab.com/foo/bar", "host": "gitlab.com"}
  ],
  "next_cursor": null,
  "total": 1
}
```

## `/api/v1` deprecation

`/api/v1` continues to work for `github.com` seeds in v3. Any seed whose
host is not `github.com` is rejected:

```
HTTP/1.1 400 Bad Request
{"error": "v1 supports github.com seeds only; use /api/v2"}
```

The `/api/v1` endpoints will be **removed in v4**. Migrate to
`/api/v2` — the request bodies are a superset (v1 fields are
forward-compatible) and the response is enriched with the `subkind`
discriminator.

## Quick curl examples

```bash
# Pick the right base URL for your deploy
export API_BASE="http://localhost:8000/api/v1"      # uvicorn directly
# export API_BASE="http://localhost/api/v1"         # behind Nginx
export API_TOKEN="my-secret-api-token"

# Start a crawl
JOB_ID=$(curl -s -X POST "$API_BASE/crawl" \
  -H "Authorization: Bearer $API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"seeds":["torvalds"],"max_rounds":2}' \
  | python -c 'import sys, json; print(json.load(sys.stdin)["job_id"])')

# Poll status until completed
while :; do
  curl -s "$API_BASE/crawl/$JOB_ID" \
    -H "Authorization: Bearer $API_TOKEN" \
    | python -m json.tool
  sleep 5
done

# Pause / resume / cancel
curl -X POST "$API_BASE/crawl/$JOB_ID/pause"  -H "Authorization: Bearer $API_TOKEN"
curl -X POST "$API_BASE/crawl/$JOB_ID/resume" -H "Authorization: Bearer $API_TOKEN"
curl -X POST "$API_BASE/crawl/$JOB_ID/cancel" -H "Authorization: Bearer $API_TOKEN"

# List jobs (optionally filtered)
curl "$API_BASE/jobs?status_filter=completed" -H "Authorization: Bearer $API_TOKEN"

# Fetch the graph once completed
curl "$API_BASE/graph/$JOB_ID" -H "Authorization: Bearer $API_TOKEN"

# Drop a terminal job
curl -X DELETE "$API_BASE/crawl/$JOB_ID" -H "Authorization: Bearer $API_TOKEN"
```

## Running the server

```bash
export API_TOKEN="my-secret"
export CRAWLER_PLATFORMS="github.com,gitlab.com"
export CRAWLER_TOKEN__GITHUB_COM="ghp_..."
export CRAWLER_TOKEN__GITLAB_COM="glpat-..."
export OPC_DATA_DIR="/var/lib/crawler/jobs"     # optional; per-job snapshots + resumable state
export OPC_CACHE_DIR="$OPC_DATA_DIR/cache"      # optional; GitHub/GitLab API response cache ("" disables)
export OPC_CACHE_TTL_DAYS="30"                  # optional; cache entry expiry (0 = never expire)
uvicorn open_pulse_crawler.api:app --host 0.0.0.0 --port 8000
```

GitHub API responses are cached so repeat crawls skip network calls; entries
expire after `OPC_CACHE_TTL_DAYS` (default 30). See
[`docs/DEPLOYMENT.md` → Caching](./DEPLOYMENT.md#caching) for the full behavior.

For the full Docker Compose + Nginx stack, see
[`docs/DEPLOYMENT.md`](./DEPLOYMENT.md). For concurrency, rate limiting, and multi-token
rotation, see [`docs/CONCURRENCY.md`](./CONCURRENCY.md).
