"""FastAPI REST API for the Open Pulse Crawler."""

from __future__ import annotations

import logging
import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import BackgroundTasks, Body, Depends, FastAPI, HTTPException, status
from pydantic import BaseModel, Field

from . import __version__
from .auth import verify_token
from .models import GraphData

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class JobStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class CrawlRequest(BaseModel):
    seeds: List[str] = Field(..., min_length=1, description="Seed nodes (users, orgs, or repos)")
    max_rounds: int = Field(default=2, ge=1, le=10, description="BFS rounds")
    crawl_dependencies: bool = Field(
        default=False, description="Crawl repository dependencies (downstream)"
    )
    crawl_dependents: bool = Field(
        default=False, description="Crawl repository dependents (upstream)"
    )
    min_stars: int = Field(
        default=0, ge=0, description="Minimum stars for dependency/dependent filtering"
    )
    max_dependents: Optional[int] = Field(
        default=None, ge=1, description="Maximum number of dependents to fetch"
    )
    batch_size: Optional[int] = Field(
        default=None, ge=1, description="Number of nodes to process concurrently"
    )


# Named examples surfaced in Swagger UI as a dropdown selector when editing
# the POST /api/v1/crawl request body.
_CRAWL_REQUEST_EXAMPLES = {
    "single_repo": {
        "summary": "Single repo, shallow crawl",
        "description": (
            "Smallest viable crawl: one repo seed, one BFS round. "
            "Useful as a smoke test."
        ),
        "value": {
            "seeds": ["sdsc-ordes/open-pulse-crawler"],
            "max_rounds": 1,
        },
    },
    "sdsc_ordes_no_dependents": {
        "summary": "Org without dependents",
        "description": (
            "Discover everything reachable from the sdsc-ordes org via "
            "ownership and contributor edges. Does not pull in downstream/"
            "upstream dependency repos."
        ),
        "value": {
            "seeds": ["sdsc-ordes"],
            "max_rounds": 2,
            "crawl_dependencies": False,
            "crawl_dependents": False,
        },
    },
    "repo_with_dependents": {
        "summary": "Repo with dependents",
        "description": (
            "Repository seed plus dependent discovery — pulls in repos that "
            "depend on (use) this one. Dependents are a repo-level concept on "
            "GitHub, so this only makes sense for repository seeds, not for "
            "user/org seeds. Capped at 50 dependents and a 10-star floor."
        ),
        "value": {
            "seeds": ["sdsc-ordes/gimie"],
            "max_rounds": 2,
            "crawl_dependents": True,
            "min_stars": 10,
            "max_dependents": 50,
        },
    },
}


class CrawlJobResponse(BaseModel):
    job_id: str
    status: JobStatus
    detail: Optional[str] = None


class CrawlResultResponse(BaseModel):
    job_id: str
    status: JobStatus
    detail: Optional[str] = None
    users: int = 0
    orgs: int = 0
    repos: int = 0
    # Progress + timing — populated for running and completed jobs alike.
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    current_round: Optional[int] = None
    nodes_processed: int = 0
    nodes_in_queue: int = 0
    # Best-effort ETA based on the current processing rate. Drifts a lot mid-BFS
    # because the queue grows as the crawl expands; useful as a rough hint, not
    # a guarantee.
    estimated_completion_at: Optional[datetime] = None


class JobSummary(BaseModel):
    job_id: str
    status: JobStatus
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    users: int = 0
    orgs: int = 0
    repos: int = 0
    detail: Optional[str] = None


class JobsListResponse(BaseModel):
    jobs: List[JobSummary]
    total: int


class GraphResponse(BaseModel):
    job_id: str
    graph: dict


class HealthResponse(BaseModel):
    status: str = "ok"
    version: str


# ---------------------------------------------------------------------------
# In-memory job store
# ---------------------------------------------------------------------------


@dataclass
class _JobRecord:
    """In-memory state for a crawl job.

    Held outside Pydantic so we can store a live ``GitHubCrawler`` reference
    (for progress polling) without ``arbitrary_types_allowed`` gymnastics.
    """

    status: JobStatus = JobStatus.PENDING
    detail: Optional[str] = None
    graph: Optional[GraphData] = None
    jsonld_dir: Optional[str] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    # Live reference to the running ``GitHubCrawler`` so the status endpoint
    # can read ``current_round``, ``visited``, and ``queue`` while the BFS
    # is in flight. Cleared (or just left dangling) once the job finishes.
    crawler: Optional[Any] = field(default=None, repr=False, compare=False)


_jobs: Dict[str, _JobRecord] = {}

# ---------------------------------------------------------------------------
# Server-side gimie configuration (env-driven)
# ---------------------------------------------------------------------------
#
# These are deployment concerns, not per-job knobs — they're read from the
# environment once per crawl, not from the request body. Operators decide
# whether the gimie hybrid path is enabled and where the gimie service lives;
# clients submitting crawls don't need to know.

_DEFAULT_GIMIE_API_BASE = "http://host.docker.internal:1234"


def _bool_env(name: str, default: bool = False) -> bool:
    """Parse a boolean env var. Truthy: true / 1 / yes / on (case-insensitive)."""
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("true", "1", "yes", "on")


# ---------------------------------------------------------------------------
# Background task
# ---------------------------------------------------------------------------


def _run_crawl(
    job_id: str,
    seeds: List[str],
    max_rounds: int,
    crawl_dependencies: bool,
    crawl_dependents: bool,
    min_stars: int,
    max_dependents: Optional[int],
    batch_size: Optional[int],
) -> None:
    """Execute a crawl in the background and store results."""
    record = _jobs[job_id]
    record.status = JobStatus.RUNNING
    record.started_at = datetime.now(timezone.utc)

    try:
        from .crawler import GitHubCrawler
        from .github_client import GitHubClient

        tokens_raw = os.environ.get("GITHUB_TOKEN", "")
        tokens = [t.strip() for t in tokens_raw.split(",") if t.strip()]
        if not tokens:
            record.status = JobStatus.FAILED
            record.detail = "GITHUB_TOKEN environment variable is not set"
            record.completed_at = datetime.now(timezone.utc)
            return

        # Read gimie config from environment (deployment concern, not per-job).
        gimie_repos = _bool_env("GIMIE_ENABLED", default=False)
        gimie_api_base = os.environ.get("GIMIE_API_BASE", _DEFAULT_GIMIE_API_BASE)
        gimie_store_jsonld = _bool_env("GIMIE_STORE_JSONLD", default=False)
        gimie_skip_existing_jsonld = _bool_env(
            "GIMIE_SKIP_EXISTING_JSONLD", default=False
        )

        jsonld_dir: Optional[Path] = None
        if gimie_repos and gimie_store_jsonld:
            data_root = Path(os.environ.get("OPC_DATA_DIR", "/tmp/open-pulse-crawler"))
            jsonld_dir = data_root / job_id / "jsonld"
            jsonld_dir.mkdir(parents=True, exist_ok=True)

        client = GitHubClient(tokens=tokens)
        crawler = GitHubCrawler(
            client=client,
            max_rounds=max_rounds,
            batch_size=batch_size,
            crawl_dependencies=crawl_dependencies,
            crawl_dependents=crawl_dependents,
            min_stars=min_stars,
            max_dependents=max_dependents,
            gimie_repos=gimie_repos,
            gimie_api_base=gimie_api_base,
            gimie_store_jsonld_dir=jsonld_dir,
            gimie_skip_existing_jsonld=gimie_skip_existing_jsonld,
        )
        crawler.add_seeds(seeds)
        # Expose the live crawler + graph BEFORE running so the status endpoint
        # can read progress (visited, queue, current_round) while the BFS is in
        # flight.
        record.crawler = crawler
        record.graph = crawler.graph
        crawler.crawl(show_progress=False)

        record.graph = crawler.graph
        if jsonld_dir is not None:
            record.jsonld_dir = str(jsonld_dir)
        record.status = JobStatus.COMPLETED
        record.completed_at = datetime.now(timezone.utc)
    except Exception as exc:
        logger.exception("Crawl job %s failed", job_id)
        record.status = JobStatus.FAILED
        record.detail = str(exc)
        record.completed_at = datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# FastAPI application
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Open Pulse Crawler API",
    version=__version__,
    docs_url="/api/v1/docs",
    openapi_url="/api/v1/openapi.json",
)


@app.get("/api/v1/health", response_model=HealthResponse)
def health() -> HealthResponse:
    """Public health-check endpoint."""
    return HealthResponse(status="ok", version=__version__)


@app.post(
    "/api/v1/crawl",
    response_model=CrawlJobResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def start_crawl(
    background_tasks: BackgroundTasks,
    body: CrawlRequest = Body(..., openapi_examples=_CRAWL_REQUEST_EXAMPLES),
    _token: str = Depends(verify_token),
) -> CrawlJobResponse:
    """Start a new crawl job (runs in the background)."""
    job_id = str(uuid.uuid4())
    _jobs[job_id] = _JobRecord()
    background_tasks.add_task(
        _run_crawl,
        job_id,
        body.seeds,
        body.max_rounds,
        body.crawl_dependencies,
        body.crawl_dependents,
        body.min_stars,
        body.max_dependents,
        body.batch_size,
    )
    return CrawlJobResponse(job_id=job_id, status=JobStatus.PENDING)


def _job_progress_snapshot(record: _JobRecord) -> Dict[str, Any]:
    """Read live BFS progress from a running crawler, when available."""
    snap: Dict[str, Any] = {
        "current_round": None,
        "nodes_processed": 0,
        "nodes_in_queue": 0,
    }
    crawler = record.crawler
    if crawler is None:
        return snap
    try:
        snap["current_round"] = int(crawler.current_round)
    except (AttributeError, TypeError):
        pass
    try:
        snap["nodes_processed"] = len(crawler.visited)
    except (AttributeError, TypeError):
        pass
    try:
        snap["nodes_in_queue"] = len(crawler.queue)
    except (AttributeError, TypeError):
        pass
    return snap


def _estimate_completion(
    started_at: Optional[datetime],
    nodes_processed: int,
    nodes_in_queue: int,
) -> Optional[datetime]:
    """Best-effort ETA based on the current node-processing rate.

    Returns ``None`` until we have enough data to extrapolate (started_at is
    set, at least one node processed, queue non-empty).
    """
    if started_at is None or nodes_processed <= 0 or nodes_in_queue <= 0:
        return None
    elapsed = (datetime.now(timezone.utc) - started_at).total_seconds()
    if elapsed <= 0:
        return None
    rate = nodes_processed / elapsed  # nodes/sec
    remaining_seconds = nodes_in_queue / rate
    return datetime.now(timezone.utc) + timedelta(seconds=remaining_seconds)


@app.get("/api/v1/crawl/{job_id}", response_model=CrawlResultResponse)
def get_crawl_status(
    job_id: str,
    _token: str = Depends(verify_token),
) -> CrawlResultResponse:
    """Get status, progress, and summary of a crawl job."""
    record = _jobs.get(job_id)
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")

    resp = CrawlResultResponse(
        job_id=job_id,
        status=record.status,
        detail=record.detail,
        started_at=record.started_at,
        completed_at=record.completed_at,
    )
    if record.graph:
        resp.users = len(record.graph.users)
        resp.orgs = len(record.graph.orgs)
        resp.repos = len(record.graph.repos)

    if record.status == JobStatus.RUNNING:
        snap = _job_progress_snapshot(record)
        resp.current_round = snap["current_round"]
        resp.nodes_processed = snap["nodes_processed"]
        resp.nodes_in_queue = snap["nodes_in_queue"]
        resp.estimated_completion_at = _estimate_completion(
            record.started_at,
            snap["nodes_processed"],
            snap["nodes_in_queue"],
        )

    return resp


@app.get("/api/v1/jobs", response_model=JobsListResponse)
def list_jobs(
    status_filter: Optional[JobStatus] = None,
    _token: str = Depends(verify_token),
) -> JobsListResponse:
    """List all known jobs with status + counts.

    Optional ``status_filter`` query param narrows to one ``JobStatus``
    (e.g. ``?status_filter=completed`` for jobs ready to download).
    """
    summaries: List[JobSummary] = []
    for jid, rec in _jobs.items():
        if status_filter is not None and rec.status != status_filter:
            continue
        users = orgs = repos = 0
        if rec.graph is not None:
            users = len(rec.graph.users)
            orgs = len(rec.graph.orgs)
            repos = len(rec.graph.repos)
        summaries.append(
            JobSummary(
                job_id=jid,
                status=rec.status,
                started_at=rec.started_at,
                completed_at=rec.completed_at,
                users=users,
                orgs=orgs,
                repos=repos,
                detail=rec.detail,
            )
        )
    # Newest-first ordering: completed_at, then started_at, falls back to id.
    summaries.sort(
        key=lambda s: (
            s.completed_at or s.started_at or datetime.min.replace(tzinfo=timezone.utc),
        ),
        reverse=True,
    )
    return JobsListResponse(jobs=summaries, total=len(summaries))


@app.get("/api/v1/graph/{job_id}", response_model=GraphResponse)
def get_graph(
    job_id: str,
    _token: str = Depends(verify_token),
) -> GraphResponse:
    """Return full graph data for a completed crawl job."""
    record = _jobs.get(job_id)
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    if record.status != JobStatus.COMPLETED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Job is not completed (current status: {record.status.value})",
        )
    return GraphResponse(job_id=job_id, graph=record.graph.model_dump())
