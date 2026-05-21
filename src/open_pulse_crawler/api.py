"""FastAPI REST API for the Open Pulse Crawler."""

from __future__ import annotations

import logging
import os
import zipfile
import uuid
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, status
from fastapi.responses import FileResponse
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
    crawl_issues: bool = Field(
        default=False,
        description="Fetch issue authors and conversation commenters per repo",
    )
    crawl_prs: bool = Field(
        default=False,
        description="Fetch PR authors, conversation commenters, and reviewers per repo",
    )
    issue_max: int = Field(
        default=100, ge=1, description="Max issues to scan per repo (most recent first)"
    )
    pr_max: int = Field(
        default=100, ge=1, description="Max PRs to scan per repo (most recent first)"
    )
    batch_size: Optional[int] = Field(
        default=None, ge=1, description="Number of nodes to process concurrently"
    )
    epfl_entities: List[str] = Field(
        default_factory=list,
        description="Entity names (users/orgs) that belong to EPFL",
    )

    # ── Optional gimie JSON-LD hybrid repo fetching ─────────────────────────
    gimie_repos: bool = Field(
        default=False,
        description="Populate repository nodes from gimie JSON-LD (users/orgs still from GitHub).",
    )
    gimie_api_base: str = Field(
        default="http://host.docker.internal:1234",
        description="Base URL for the gimie JSON-LD API.",
    )
    gimie_store_jsonld: bool = Field(
        default=False,
        description="Store raw gimie JSON-LD payloads on disk under the job directory.",
    )
    gimie_skip_existing_jsonld: bool = Field(
        default=False,
        description="If storing, skip fetching JSON-LD payloads when files already exist.",
    )
    gimie_archive_on_download: bool = Field(
        default=False,
        description="If true, try to ensure jsonld.zip is created after crawl completion.",
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
    jsonld_dir: Optional[str] = None
    jsonld_zip_path: Optional[str] = None


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
    crawl_issues: bool,
    crawl_prs: bool,
    issue_max: int,
    pr_max: int,
    min_stars: int,
    max_dependents: Optional[int],
    batch_size: Optional[int],
    epfl_entities: List[str],
    gimie_repos: bool,
    gimie_api_base: str,
    gimie_store_jsonld: bool,
    gimie_skip_existing_jsonld: bool,
    gimie_archive_on_download: bool,
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

        jsonld_dir: Optional[Path] = None
        jsonld_zip_path: Optional[Path] = None
        if gimie_repos and gimie_store_jsonld:
            data_root = Path(os.environ.get("OPC_DATA_DIR", "/tmp/open-pulse-crawler"))
            job_root = data_root / job_id
            jsonld_dir = job_root / "jsonld"
            jsonld_dir.mkdir(parents=True, exist_ok=True)
            jsonld_zip_path = job_root / "jsonld.zip"

        client = GitHubClient(tokens=tokens)
        crawler = GitHubCrawler(
            client=client,
            max_rounds=max_rounds,
            batch_size=batch_size,
            crawl_dependencies=crawl_dependencies,
            crawl_dependents=crawl_dependents,
            crawl_issues=crawl_issues,
            crawl_prs=crawl_prs,
            issue_max=issue_max,
            pr_max=pr_max,
            min_stars=min_stars,
            max_dependents=max_dependents,
            epfl_entities=set(epfl_entities),
            gimie_repos=gimie_repos,
            gimie_api_base=gimie_api_base,
            gimie_store_jsonld_dir=jsonld_dir,
            gimie_skip_existing_jsonld=gimie_skip_existing_jsonld,
        )
        crawler.add_seeds(seeds)
        crawler.crawl(show_progress=False)

        record.graph = crawler.graph
        if jsonld_dir is not None:
            record.jsonld_dir = str(jsonld_dir)
            record.jsonld_zip_path = str(jsonld_zip_path) if jsonld_zip_path else None
            if gimie_archive_on_download and jsonld_zip_path:
                # Best-effort zip creation; download endpoint also works on-demand.
                try:
                    if jsonld_zip_path.exists():
                        jsonld_zip_path.unlink()
                    with zipfile.ZipFile(
                        jsonld_zip_path, "w", compression=zipfile.ZIP_DEFLATED
                    ) as zf:
                        for file_path in sorted(jsonld_dir.glob("*.json")):
                            zf.write(file_path, file_path.name)
                except Exception as exc:
                    logger.warning("Failed to create jsonld zip: %s", exc)
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
        body.crawl_issues,
        body.crawl_prs,
        body.issue_max,
        body.pr_max,
        body.min_stars,
        body.max_dependents,
        body.batch_size,
        body.epfl_entities,
        body.gimie_repos,
        body.gimie_api_base,
        body.gimie_store_jsonld,
        body.gimie_skip_existing_jsonld,
        body.gimie_archive_on_download,
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


@app.get("/api/v1/crawl/{job_id}/jsonld.zip", response_class=FileResponse)
def get_jsonld_zip(
    job_id: str,
    _token: str = Depends(verify_token),
) -> FileResponse:
    """Download stored gimie JSON-LD payloads for a completed job."""
    record = _jobs.get(job_id)
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    if record.status != JobStatus.COMPLETED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Job is not completed (current status: {record.status.value})",
        )
    if not record.jsonld_dir:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="JSON-LD payloads were not stored for this job",
        )

    jsonld_dir = Path(record.jsonld_dir)
    if not jsonld_dir.exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="JSON-LD directory missing on disk")

    # Reuse an existing zip if already created.
    if record.jsonld_zip_path:
        zip_path = Path(record.jsonld_zip_path)
        if zip_path.exists():
            return FileResponse(path=str(zip_path), filename="jsonld.zip", media_type="application/zip")

    job_root = jsonld_dir.parent
    zip_path = job_root / "jsonld.zip"
    try:
        if zip_path.exists():
            zip_path.unlink()
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for file_path in sorted(jsonld_dir.glob("*.json")):
                zf.write(file_path, file_path.name)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to create jsonld zip: {exc}") from exc

    return FileResponse(path=str(zip_path), filename="jsonld.zip", media_type="application/zip")
