# Open Pulse Crawler — REST API

Base path: `/api/v1`

Interactive Swagger docs are available at `/api/v1/docs` when the server is running.

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

```json
{
  "seeds": ["torvalds", "sdsc-ordes/gimie"],
  "max_rounds": 3
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

## Running the Server

```bash
export API_TOKEN="my-secret"
export GITHUB_TOKEN="ghp_..."
uvicorn open_pulse_crawler.api:app --host 0.0.0.0 --port 8000
```
