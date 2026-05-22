"""FastAPI REST API for the Open Pulse Crawler."""

from __future__ import annotations

import json
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
    PAUSED = "paused"
    CANCELLED = "cancelled"
    COMPLETED = "completed"
    FAILED = "failed"


# States the BFS loop has stopped or never started running. Used by the
# DELETE endpoint so we don't drop a still-active job out from under itself.
_TERMINAL_STATES = {
    JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED, JobStatus.PENDING,
}


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
    max_contributors: Optional[int] = Field(
        default=None,
        ge=1,
        description=(
            "Skip contributor expansion for repos with more than N contributors. "
            "The repo node stays in the graph; only its contributor users are "
            "not queued. Useful for avoiding mega-projects."
        ),
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
    "epfl_with_megaproject_skip": {
        "summary": "Skip mega-projects by contributor count",
        "description": (
            "Crawl from an EPFL-flavoured seed list and skip contributor "
            "expansion for any repo with more than 200 contributors. The repo "
            "node still lands in the graph (with owner / fork / dependency "
            "edges if those are enabled), but its contributors are not queued "
            "as new BFS frontier nodes. The first time a repo is fetched the "
            "count is captured and cached, so subsequent crawls re-apply the "
            "rule without further API calls."
        ),
        "value": {
            "seeds": ["epfl", "dslab-epfl", "sdsc-ordes/gimie"],
            "max_rounds": 2,
            "max_contributors": 200,
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
    # True when the graph is a partial snapshot (job not COMPLETED, or the
    # in-memory record was lost and only an on-disk snapshot remains).
    partial: bool = False
    status: Optional[JobStatus] = None
    rounds_completed: Optional[int] = None


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
    # Number of BFS rounds completed so far — kept in sync per round so a
    # partial-graph reader knows how much of the crawl is reflected.
    rounds_completed: int = 0
    graph_snapshot_path: Optional[str] = None
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
# Per-round disk snapshots (partial-graph recovery + resume)
# ---------------------------------------------------------------------------
#
# The job store is in-memory, so a partial graph and resumable state would be
# lost on container restart. The background crawl writes the graph to disk
# after every BFS round: GET /graph?partial=true reads those snapshots, and
# POST .../resume continues a cancelled/failed job from the crawler's own
# state file. All paths live under OPC_DATA_DIR/{job_id}/.


def _snapshot_dir(job_id: str) -> Path:
    data_root = Path(os.environ.get("OPC_DATA_DIR", "/tmp/open-pulse-crawler"))
    return data_root / job_id


def _state_path(job_id: str) -> Path:
    """Crawler state file (queue + visited + graph) — used by resume."""
    return _snapshot_dir(job_id) / "state.json"


def _request_path(job_id: str) -> Path:
    """Persisted crawl request — lets resume rebuild the exact config."""
    return _snapshot_dir(job_id) / "request.json"


def _write_snapshot(
    job_id: str, graph: GraphData, status_value: JobStatus, rounds_completed: int
) -> Optional[str]:
    """Atomically write the current graph + metadata to disk. Best-effort."""
    try:
        job_dir = _snapshot_dir(job_id)
        job_dir.mkdir(parents=True, exist_ok=True)
        path = job_dir / "graph.snapshot.json"
        payload = {
            "job_id": job_id,
            "status": status_value.value,
            "rounds_completed": rounds_completed,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "graph": graph.model_dump(),
        }
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload))
        tmp.replace(path)  # atomic: readers never see a half-written file
        return str(path)
    except Exception as exc:  # snapshotting must never break the crawl
        logger.warning("Failed to write graph snapshot for %s: %s", job_id, exc)
        return None


def _read_snapshot(job_id: str) -> Optional[Dict[str, Any]]:
    """Read a graph snapshot from disk. Returns the full payload dict or None."""
    try:
        path = _snapshot_dir(job_id) / "graph.snapshot.json"
        if not path.exists():
            return None
        return json.loads(path.read_text())
    except Exception as exc:
        logger.warning("Failed to read graph snapshot for %s: %s", job_id, exc)
        return None


def _install_snapshotting(crawler: Any, record: "_JobRecord", job_id: str) -> None:
    """Point record.graph at the live GraphData and snapshot per round.

    ``GraphData`` is mutated in place by the crawler, so binding it to the
    record now keeps a cancelled/failed job's partial graph available.
    """
    record.graph = crawler.graph
    record.rounds_completed = crawler.current_round  # 0 fresh, restored on resume

    def _on_round(round_index: int) -> None:
        record.rounds_completed = round_index + 1
        record.graph_snapshot_path = _write_snapshot(
            job_id, crawler.graph, JobStatus.RUNNING, round_index + 1
        )

    crawler.incremental_export_callback = _on_round


def _persist_request(job_id: str, body: "CrawlRequest", mode: str) -> None:
    """Persist the crawl request so a resume can rebuild the same config.

    ``mode`` is "rest" or "graphql" — resume re-dispatches the matching task.
    """
    try:
        job_dir = _snapshot_dir(job_id)
        job_dir.mkdir(parents=True, exist_ok=True)
        path = _request_path(job_id)
        payload = {"mode": mode, "request": body.model_dump()}
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload))
        tmp.replace(path)
    except Exception as exc:
        logger.warning("Failed to persist crawl request for %s: %s", job_id, exc)


def _load_persisted_request(job_id: str) -> Optional[Dict[str, Any]]:
    """Return {'mode': ..., 'request': {...}} for a job, or None if absent."""
    try:
        path = _request_path(job_id)
        if not path.exists():
            return None
        return json.loads(path.read_text())
    except Exception as exc:
        logger.warning("Failed to read persisted request for %s: %s", job_id, exc)
        return None


def _seed_or_resume(crawler: Any, seeds: List[str], job_id: str, resume: bool) -> None:
    """Resume from the persisted state file when asked and possible, else seed.

    ``load_state()`` replaces ``crawler.graph``, so this runs before
    ``_install_snapshotting`` (which captures the live graph reference).
    """
    if resume and _state_path(job_id).exists():
        if crawler.load_state():
            logger.info("Resuming job %s from saved crawler state", job_id)
            return
        logger.warning("Job %s: saved state unusable, starting fresh", job_id)
    crawler.add_seeds(seeds)


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
    max_contributors: Optional[int],
    crawl_issues: bool,
    crawl_prs: bool,
    issue_max: int,
    pr_max: int,
    batch_size: Optional[int],
    resume: bool = False,
) -> None:
    """Execute a REST-backed crawl in the background and store results.

    With ``resume=True`` and a saved state file present, the crawl continues
    from the persisted BFS state instead of re-crawling from the seeds.
    """
    record = _jobs[job_id]
    record.status = JobStatus.RUNNING
    record.started_at = datetime.now(timezone.utc)

    try:
        from .crawler import GitHubCrawler
        from .github_client import GitHubClient, resolve_cache_dir

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
            jsonld_dir = _snapshot_dir(job_id) / "jsonld"
            jsonld_dir.mkdir(parents=True, exist_ok=True)

        client = GitHubClient(tokens=tokens, cache_dir=resolve_cache_dir())
        crawler = GitHubCrawler(
            client=client,
            max_rounds=max_rounds,
            state_file=_state_path(job_id),
            batch_size=batch_size,
            crawl_dependencies=crawl_dependencies,
            crawl_dependents=crawl_dependents,
            crawl_issues=crawl_issues,
            crawl_prs=crawl_prs,
            issue_max=issue_max,
            pr_max=pr_max,
            min_stars=min_stars,
            max_dependents=max_dependents,
            max_contributors=max_contributors,
            gimie_repos=gimie_repos,
            gimie_api_base=gimie_api_base,
            gimie_store_jsonld_dir=jsonld_dir,
            gimie_skip_existing_jsonld=gimie_skip_existing_jsonld,
        )
        # Expose the live crawler so the status endpoint can read progress.
        record.crawler = crawler
        _seed_or_resume(crawler, seeds, job_id, resume)
        _install_snapshotting(crawler, record, job_id)
        crawler.crawl(show_progress=False)

        if jsonld_dir is not None:
            record.jsonld_dir = str(jsonld_dir)
        # A cancel mid-flight makes the crawler exit its loop cleanly; reflect
        # that so callers know the graph is partial, not "completed".
        if crawler.cancel_requested:
            record.status = JobStatus.CANCELLED
            record.detail = record.detail or "cancelled via /cancel"
        else:
            record.status = JobStatus.COMPLETED
        record.completed_at = datetime.now(timezone.utc)
        _write_snapshot(job_id, crawler.graph, record.status, record.rounds_completed)
    except Exception as exc:
        logger.exception("Crawl job %s failed", job_id)
        record.status = JobStatus.FAILED
        record.detail = str(exc)
        record.completed_at = datetime.now(timezone.utc)
        if record.graph is not None:
            _write_snapshot(
                job_id, record.graph, JobStatus.FAILED, record.rounds_completed
            )


def _run_crawl_graphql(
    job_id: str,
    seeds: List[str],
    max_rounds: int,
    crawl_dependencies: bool,
    crawl_dependents: bool,
    min_stars: int,
    max_dependents: Optional[int],
    max_contributors: Optional[int],
    crawl_issues: bool,
    crawl_prs: bool,
    issue_max: int,
    pr_max: int,
    batch_size: Optional[int],
    resume: bool = False,
) -> None:
    """Execute a GraphQL-backed crawl in the background.

    Reuses GitHubCrawler via a GraphQL client that returns cached-shape
    dicts. SBOM dependencies and the "Used by" dependents graph still go
    via REST. Stop (cancel) and resume behave as for the REST crawl.
    """
    record = _jobs[job_id]
    record.status = JobStatus.RUNNING
    record.started_at = datetime.now(timezone.utc)

    try:
        from .crawler import GitHubCrawler
        from .github_client import resolve_cache_dir
        from .graphql_client import GitHubGraphQLClient

        tokens_raw = os.environ.get("GITHUB_TOKEN", "")
        tokens = [t.strip() for t in tokens_raw.split(",") if t.strip()]
        if not tokens:
            record.status = JobStatus.FAILED
            record.detail = "GITHUB_TOKEN environment variable is not set"
            record.completed_at = datetime.now(timezone.utc)
            return

        client = GitHubGraphQLClient(
            tokens=tokens,
            cache_dir=resolve_cache_dir(),
            crawl_issues=crawl_issues,
            crawl_prs=crawl_prs,
            issue_max=issue_max,
            pr_max=pr_max,
        )
        crawler = GitHubCrawler(
            client=client,
            max_rounds=max_rounds,
            state_file=_state_path(job_id),
            batch_size=batch_size,
            crawl_dependencies=crawl_dependencies,
            crawl_dependents=crawl_dependents,
            crawl_issues=crawl_issues,
            crawl_prs=crawl_prs,
            issue_max=issue_max,
            pr_max=pr_max,
            min_stars=min_stars,
            max_dependents=max_dependents,
            max_contributors=max_contributors,
        )
        record.crawler = crawler
        _seed_or_resume(crawler, seeds, job_id, resume)
        _install_snapshotting(crawler, record, job_id)
        crawler.crawl(show_progress=False)

        if crawler.cancel_requested:
            record.status = JobStatus.CANCELLED
            record.detail = record.detail or "cancelled via /cancel"
        else:
            record.status = JobStatus.COMPLETED
        record.completed_at = datetime.now(timezone.utc)
        _write_snapshot(job_id, crawler.graph, record.status, record.rounds_completed)
    except Exception as exc:
        logger.exception("GraphQL crawl job %s failed", job_id)
        record.status = JobStatus.FAILED
        record.detail = str(exc)
        record.completed_at = datetime.now(timezone.utc)
        if record.graph is not None:
            _write_snapshot(
                job_id, record.graph, JobStatus.FAILED, record.rounds_completed
            )


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
    _persist_request(job_id, body, mode="rest")
    background_tasks.add_task(
        _run_crawl,
        job_id,
        body.seeds,
        body.max_rounds,
        body.crawl_dependencies,
        body.crawl_dependents,
        body.min_stars,
        body.max_dependents,
        body.max_contributors,
        body.crawl_issues,
        body.crawl_prs,
        body.issue_max,
        body.pr_max,
        body.batch_size,
    )
    return CrawlJobResponse(job_id=job_id, status=JobStatus.PENDING)


@app.post(
    "/api/v1/crawl/graphql",
    response_model=CrawlJobResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def start_crawl_graphql(
    background_tasks: BackgroundTasks,
    body: CrawlRequest = Body(..., openapi_examples=_CRAWL_REQUEST_EXAMPLES),
    _token: str = Depends(verify_token),
) -> CrawlJobResponse:
    """Start a crawl using the GraphQL-backed client.

    Same request body as ``POST /api/v1/crawl``. GraphQL covers user / org /
    repo metadata, follows / stars / watching, teams, and issue/PR activity;
    contributors, SBOM dependencies, and the "Used by" dependents graph fall
    back to REST. Gimie hybrid mode is not available on this endpoint.
    """
    job_id = str(uuid.uuid4())
    _jobs[job_id] = _JobRecord()
    _persist_request(job_id, body, mode="graphql")
    background_tasks.add_task(
        _run_crawl_graphql,
        job_id,
        body.seeds,
        body.max_rounds,
        body.crawl_dependencies,
        body.crawl_dependents,
        body.min_stars,
        body.max_dependents,
        body.max_contributors,
        body.crawl_issues,
        body.crawl_prs,
        body.issue_max,
        body.pr_max,
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
    partial: bool = False,
    _token: str = Depends(verify_token),
) -> GraphResponse:
    """Return graph data for a crawl job.

    Default (strict): only a COMPLETED job held in memory is served. Pass
    ``?partial=true`` to also read a partial graph — from a running /
    cancelled / failed job, or, if the in-memory record is gone (container
    restart), from the last per-round disk snapshot keyed by ``job_id``.
    """
    record = _jobs.get(job_id)

    # Strict path: completed job held in memory — unchanged semantics.
    if (
        record is not None
        and record.status == JobStatus.COMPLETED
        and record.graph is not None
    ):
        return GraphResponse(
            job_id=job_id,
            graph=record.graph.model_dump(),
            partial=False,
            status=record.status,
            rounds_completed=record.rounds_completed,
        )

    if not partial:
        if record is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Job not found"
            )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Job is not completed (current status: {record.status.value}). "
                f"Pass ?partial=true to read partial output."
            ),
        )

    # Partial read: prefer the on-disk snapshot (always round-consistent,
    # written atomically between rounds).
    snapshot = _read_snapshot(job_id)
    if snapshot is not None:
        snap_status = snapshot.get("status")
        return GraphResponse(
            job_id=job_id,
            graph=snapshot.get("graph") or {},
            partial=snap_status != JobStatus.COMPLETED.value,
            status=snap_status,
            rounds_completed=snapshot.get("rounds_completed"),
        )

    # No snapshot on disk — fall back to the in-memory graph if present.
    if record is not None and record.graph is not None:
        return GraphResponse(
            job_id=job_id,
            graph=record.graph.model_dump(),
            partial=record.status != JobStatus.COMPLETED,
            status=record.status,
            rounds_completed=record.rounds_completed,
        )

    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Job not found (no in-memory record and no disk snapshot)",
        )
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail=f"No graph snapshot available yet (status: {record.status.value})",
    )


# ── Job lifecycle controls ─────────────────────────────────────────────────
#
# Pause / resume / cancel are cooperative — they set flags on the live
# GitHubCrawler instance which the BFS loop checks between rounds.
# Mid-round network calls finish before the loop exits, which keeps the
# graph in a consistent state (partial but not torn).

class JobActionResponse(BaseModel):
    job_id: str
    status: JobStatus
    detail: Optional[str] = None


def _job_or_404(job_id: str) -> _JobRecord:
    record = _jobs.get(job_id)
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    return record


@app.post("/api/v1/crawl/{job_id}/pause", response_model=JobActionResponse)
def pause_crawl(
    job_id: str,
    _token: str = Depends(verify_token),
) -> JobActionResponse:
    """Request the BFS loop to pause between rounds.

    The currently in-flight round drains first; once the round boundary is
    hit the loop sleeps in 1-second ticks until ``resume`` (or ``cancel``)
    is called. Status flips to ``paused`` immediately.
    """
    record = _job_or_404(job_id)
    if record.status not in (JobStatus.RUNNING, JobStatus.PAUSED):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot pause a job in state {record.status.value!r}",
        )
    if record.crawler is not None:
        record.crawler.pause_requested = True
    record.status = JobStatus.PAUSED
    record.detail = "paused"
    return JobActionResponse(job_id=job_id, status=record.status, detail=record.detail)


@app.post("/api/v1/crawl/{job_id}/resume", response_model=JobActionResponse)
def resume_crawl(
    job_id: str,
    background_tasks: BackgroundTasks,
    _token: str = Depends(verify_token),
) -> JobActionResponse:
    """Resume a job.

    For a ``paused`` job this lifts the pause and the BFS loop continues.
    For a ``cancelled`` or ``failed`` job — or one whose in-memory record was
    lost on a container restart — it re-dispatches the crawl from the
    persisted BFS state (queue + visited set + graph) instead of the seeds,
    as long as ``state.json`` + ``request.json`` are on disk.
    """
    record = _jobs.get(job_id)

    # Live paused job: just lift the pause flag, no re-dispatch.
    if record is not None and record.status == JobStatus.PAUSED:
        if record.crawler is not None:
            record.crawler.pause_requested = False
        record.status = JobStatus.RUNNING
        record.detail = None
        return JobActionResponse(job_id=job_id, status=record.status)

    # A still-active job cannot be resumed-from-state.
    if record is not None and record.status in (JobStatus.RUNNING, JobStatus.PENDING):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot resume a job in state {record.status.value!r}",
        )

    # Resume-from-state: needs a persisted request + a saved crawler state.
    saved = _load_persisted_request(job_id)
    if saved is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No persisted request for this job; cannot resume",
        )
    if not _state_path(job_id).exists():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="No saved crawler state for this job; nothing to resume",
        )
    try:
        body = CrawlRequest(**saved["request"])
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Persisted request is invalid: {exc}",
        )

    task = _run_crawl_graphql if saved.get("mode") == "graphql" else _run_crawl
    _jobs[job_id] = _JobRecord()
    background_tasks.add_task(
        task,
        job_id,
        body.seeds,
        body.max_rounds,
        body.crawl_dependencies,
        body.crawl_dependents,
        body.min_stars,
        body.max_dependents,
        body.max_contributors,
        body.crawl_issues,
        body.crawl_prs,
        body.issue_max,
        body.pr_max,
        body.batch_size,
        True,  # resume=True
    )
    return JobActionResponse(
        job_id=job_id,
        status=JobStatus.PENDING,
        detail="resuming crawl from saved state",
    )


@app.post("/api/v1/crawl/{job_id}/cancel", response_model=JobActionResponse)
def cancel_crawl(
    job_id: str,
    _token: str = Depends(verify_token),
) -> JobActionResponse:
    """Ask the BFS loop to stop at the next round boundary.

    The job's final ``status`` becomes ``cancelled`` once the loop exits.
    The graph collected so far is preserved and accessible (partial).
    """
    record = _job_or_404(job_id)
    if record.status not in (JobStatus.RUNNING, JobStatus.PAUSED, JobStatus.PENDING):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot cancel a job in state {record.status.value!r}",
        )
    if record.crawler is not None:
        record.crawler.cancel_requested = True
        # If currently paused, also lift the pause so the loop wakes up
        # and notices the cancel flag.
        record.crawler.pause_requested = False
    # Leave status as-is for now — the worker thread will flip to
    # CANCELLED when the loop actually exits, so callers see the
    # transition rather than a phantom.
    record.detail = "cancellation requested"
    return JobActionResponse(job_id=job_id, status=record.status, detail=record.detail)


@app.delete("/api/v1/crawl/{job_id}", response_model=JobActionResponse)
def delete_crawl(
    job_id: str,
    _token: str = Depends(verify_token),
) -> JobActionResponse:
    """Drop a terminal job from the in-memory registry.

    Refuses to delete a still-active job (RUNNING / PAUSED) — cancel it
    first. Useful for clearing the listing in long-lived deployments.
    """
    record = _job_or_404(job_id)
    if record.status not in _TERMINAL_STATES:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Job is still active ({record.status.value}); cancel it first.",
        )
    final_status = record.status
    del _jobs[job_id]
    return JobActionResponse(job_id=job_id, status=final_status, detail="deleted")
