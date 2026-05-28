"""Open Pulse Crawler REST API.

Instantiates the top-level FastAPI app and mounts the versioned routers.
"""

from __future__ import annotations

from fastapi import FastAPI

from .. import __version__
from .deps import (
    CrawlJobResponse,
    CrawlRequest,
    CrawlResultResponse,
    GraphResponse,
    HealthResponse,
    JobsListResponse,
    JobStatus,
    JobSummary,
    _JobRecord,
    _api_cache_dir,
    _bool_env,
    _data_root,
    _install_snapshotting,
    _jobs,
    _load_persisted_request,
    _persist_request,
    _read_snapshot,
    _request_path,
    _run_crawl,
    _run_crawl_graphql,
    _seed_or_resume,
    _snapshot_dir,
    _state_path,
    _write_snapshot,
)
from .v1 import router as v1_router

_API_DESCRIPTION = """
A breadth-first crawler that maps relationships between GitHub **users**,
**organizations**, and **repositories**.

### Typical flow

1. `POST /api/v1/crawl` — submit seeds, get a `job_id` back immediately.
   (`POST /api/v1/crawl/graphql` is the GraphQL-backed variant — same body,
   far fewer API calls.)
2. `GET /api/v1/crawl/{job_id}` — poll for live progress: current round,
   nodes processed, queue size, and a best-effort ETA.
3. `GET /api/v1/graph/{job_id}` — fetch the discovered graph once the job is
   `completed`. Add `?partial=true` to read a partial graph from a running,
   cancelled, or failed job.

Long-running jobs can be **paused**, **cancelled**, and **resumed** — see the
*Job lifecycle* endpoints. A cancelled or failed job can be resumed from its
last persisted state instead of re-crawling from scratch.

### Authentication

Every endpoint except `GET /api/v1/health` requires a Bearer token, validated
against the `API_TOKEN` environment variable:

```
Authorization: Bearer <token>
```
"""

_OPENAPI_TAGS = [
    {"name": "Health", "description": "Unauthenticated liveness check."},
    {
        "name": "Crawl",
        "description": "Submit crawl jobs and read their status, progress, and results.",
    },
    {
        "name": "Job lifecycle",
        "description": (
            "Pause, resume, cancel, and delete jobs. Pause/cancel are "
            "cooperative — they take effect at the next BFS round boundary."
        ),
    },
    {
        "name": "Graph",
        "description": "Fetch the discovered graph — full (completed jobs) or partial.",
    },
]

app = FastAPI(
    title="Open Pulse Crawler API",
    version=__version__,
    description=_API_DESCRIPTION,
    openapi_tags=_OPENAPI_TAGS,
    contact={
        "name": "Open Pulse Crawler",
        "url": "https://github.com/sdsc-ordes/open-pulse-crawler",
    },
    license_info={"name": "See repository LICENSE"},
    docs_url="/api/v1/docs",
    openapi_url="/api/v1/openapi.json",
)
app.include_router(v1_router, prefix="/api/v1")


__all__ = [
    "app",
    # DTOs re-exported for legacy import sites.
    "CrawlJobResponse",
    "CrawlRequest",
    "CrawlResultResponse",
    "GraphResponse",
    "HealthResponse",
    "JobsListResponse",
    "JobStatus",
    "JobSummary",
    # Internal helpers re-exported for tests that imported them from
    # ``open_pulse_crawler.api`` before the api/ package split.
    "_JobRecord",
    "_api_cache_dir",
    "_bool_env",
    "_data_root",
    "_install_snapshotting",
    "_jobs",
    "_load_persisted_request",
    "_persist_request",
    "_read_snapshot",
    "_request_path",
    "_run_crawl",
    "_run_crawl_graphql",
    "_seed_or_resume",
    "_snapshot_dir",
    "_state_path",
    "_write_snapshot",
]
