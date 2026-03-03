"""FastAPI REST API for the Open Pulse Crawler."""

from __future__ import annotations

import logging
import os
import uuid
from enum import Enum
from typing import Dict, List, Optional

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, status
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
    epfl_entities: List[str] = Field(
        default_factory=list,
        description="Entity names (users/orgs) that belong to EPFL",
    )


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


class GraphResponse(BaseModel):
    job_id: str
    graph: dict


class HealthResponse(BaseModel):
    status: str = "ok"
    version: str


# ---------------------------------------------------------------------------
# In-memory job store
# ---------------------------------------------------------------------------


class _JobRecord(BaseModel):
    status: JobStatus = JobStatus.PENDING
    detail: Optional[str] = None
    graph: Optional[GraphData] = None


_jobs: Dict[str, _JobRecord] = {}

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
    epfl_entities: List[str],
) -> None:
    """Execute a crawl in the background and store results."""
    record = _jobs[job_id]
    record.status = JobStatus.RUNNING

    try:
        from .crawler import GitHubCrawler
        from .github_client import GitHubClient

        tokens_raw = os.environ.get("GITHUB_TOKEN", "")
        tokens = [t.strip() for t in tokens_raw.split(",") if t.strip()]
        if not tokens:
            record.status = JobStatus.FAILED
            record.detail = "GITHUB_TOKEN environment variable is not set"
            return

        client = GitHubClient(tokens=tokens)
        crawler = GitHubCrawler(
            client=client,
            max_rounds=max_rounds,
            batch_size=batch_size,
            crawl_dependencies=crawl_dependencies,
            crawl_dependents=crawl_dependents,
            min_stars=min_stars,
            max_dependents=max_dependents,
            epfl_entities=set(epfl_entities),
        )
        crawler.add_seeds(seeds)
        crawler.crawl(show_progress=False)

        record.graph = crawler.graph
        record.status = JobStatus.COMPLETED
    except Exception as exc:
        logger.exception("Crawl job %s failed", job_id)
        record.status = JobStatus.FAILED
        record.detail = str(exc)


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
    body: CrawlRequest,
    background_tasks: BackgroundTasks,
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
        body.epfl_entities,
    )
    return CrawlJobResponse(job_id=job_id, status=JobStatus.PENDING)


@app.get("/api/v1/crawl/{job_id}", response_model=CrawlResultResponse)
def get_crawl_status(
    job_id: str,
    _token: str = Depends(verify_token),
) -> CrawlResultResponse:
    """Get status and summary of a crawl job."""
    record = _jobs.get(job_id)
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")

    resp = CrawlResultResponse(
        job_id=job_id,
        status=record.status,
        detail=record.detail,
    )
    if record.graph:
        resp.users = len(record.graph.users)
        resp.orgs = len(record.graph.orgs)
        resp.repos = len(record.graph.repos)
    return resp


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
