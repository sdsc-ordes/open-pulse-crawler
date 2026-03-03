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

```json
{
  "seeds": ["torvalds", "sdsc-ordes/gimie"],
  "max_rounds": 3,
  "crawl_dependents": true,
  "min_stars": 10,
  "max_dependents": 100,
  "epfl_entities": ["epfl", "dslab-epfl"]
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
  "repos": 87
}
```

Possible `status` values: `pending`, `running`, `completed`, `failed`.

Returns `404` if the job ID is unknown.

---

### `GET /api/v1/graph/{job_id}`

Return the full graph data for a **completed** crawl job.

**Response** `200`

```json
{
  "job_id": "d290f1ee-6c54-4b01-90e6-d701748f0851",
  "graph": {
    "users": { "torvalds": { "login": "torvalds", "..." : "..." } },
    "orgs": {},
    "repos": {}
  }
}
```

Returns `404` if the job ID is unknown, or `409 Conflict` if the job has not completed yet.

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

## Running the Server

```bash
export API_TOKEN="my-secret"
export GITHUB_TOKEN="ghp_..."
uvicorn open_pulse_crawler.api:app --host 0.0.0.0 --port 8000
```
