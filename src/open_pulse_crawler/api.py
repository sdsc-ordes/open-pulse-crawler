"""FastAPI REST API for the Open Pulse Crawler."""

from __future__ import annotations

import json
import logging
import os
import threading
import zipfile
import uuid
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

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
    STOPPED = "stopped"


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
    rounds_completed: int = 0


class GraphResponse(BaseModel):
    job_id: str
    graph: dict
    # True when the graph is a partial snapshot (job not COMPLETED, or the
    # job record was lost — e.g. container restart — and only disk remains).
    partial: bool = False
    status: Optional[JobStatus] = None
    rounds_completed: Optional[int] = None


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
    rounds_completed: int = 0
    graph_snapshot_path: Optional[str] = None


_jobs: Dict[str, _JobRecord] = {}

# Cooperative-stop signals, keyed by job_id. Created when a crawl is
# dispatched, set by POST /stop, removed when the background task exits.
# In-memory only — a stop is meaningful only for a live process.
_stop_events: Dict[str, threading.Event] = {}

# ---------------------------------------------------------------------------
# Graph snapshots (per-round, on disk)
# ---------------------------------------------------------------------------
#
# The job store above is in-memory only, so a partial graph is lost on
# container restart/OOM and a non-COMPLETED job has nothing to serve. To
# make partial results recoverable, the background crawl writes the graph
# to disk after every BFS round (and once more on the terminal transition).
# `GET /api/v1/graph/{job_id}?partial=true` reads these snapshots, which
# also lets a consumer recover by job_id after the in-memory record is gone.


def _snapshot_dir(job_id: str) -> Path:
    data_root = Path(os.environ.get("OPC_DATA_DIR", "/tmp/open-pulse-crawler"))
    return data_root / job_id


def _state_path(job_id: str) -> Path:
    """Path to the crawler's full resumable state file (queue + visited + graph)."""
    return _snapshot_dir(job_id) / "state.json"


def _request_path(job_id: str) -> Path:
    """Path to the persisted crawl request — needed to reconstruct config on resume."""
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


def _install_snapshotting(crawler, record: "_JobRecord", job_id: str) -> None:
    """Point record.graph at the live GraphData and snapshot per round.

    `GraphData` is mutated in place by the crawler, so assigning it to the
    record now means a FAILED/partial job still exposes whatever rounds
    completed — without waiting for the success path that previously was
    the only place `record.graph` got set.
    """
    record.graph = crawler.graph
    # current_round is 0 for a fresh crawl, or the restored value after a
    # resume — keep the record in sync before the first new round lands.
    record.rounds_completed = crawler.current_round

    def _on_round(round_index: int) -> None:
        record.rounds_completed = round_index + 1
        record.graph_snapshot_path = _write_snapshot(
            job_id, crawler.graph, JobStatus.RUNNING, round_index + 1
        )

    crawler.incremental_export_callback = _on_round


def _persist_request(job_id: str, body: "CrawlRequest", mode: str) -> None:
    """Persist the crawl request so a resume can rebuild the same config.

    `mode` is "rest" or "graphql" — resume must re-dispatch the matching
    background task. Best-effort; resume simply won't be available if this
    write fails.
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


def _finalize_job(job_id: str, record: "_JobRecord", crawler) -> None:
    """Set the terminal status after crawl() returns and write a final snapshot.

    STOPPED only when the stop signal actually cut the crawl short — i.e.
    a stop was requested *and* there was still work left (queued nodes and
    rounds below the cap). A stop that arrives after the crawl already
    exhausted its work is a no-op: the job is COMPLETED.
    """
    event = _stop_events.get(job_id)
    stop_requested = event is not None and event.is_set()
    work_remained = bool(crawler.queue) and crawler.current_round < crawler.max_rounds
    final = JobStatus.STOPPED if (stop_requested and work_remained) else JobStatus.COMPLETED
    record.status = final
    _write_snapshot(job_id, crawler.graph, final, record.rounds_completed)


def _seed_or_resume(crawler, body: "CrawlRequest", job_id: str, resume: bool) -> None:
    """Resume from the persisted state file when asked and possible, else seed fresh.

    `load_state()` replaces `crawler.graph`, so this must run *before*
    `_install_snapshotting` (which captures the live graph reference).
    """
    if resume and _state_path(job_id).exists():
        if crawler.load_state():
            logger.info("Resuming job %s from saved crawler state", job_id)
            return
        logger.warning("Job %s: saved state unusable, starting fresh", job_id)
    crawler.add_seeds(body.seeds)


# ---------------------------------------------------------------------------
# Background task
# ---------------------------------------------------------------------------


def _run_crawl(job_id: str, body: "CrawlRequest", resume: bool = False) -> None:
    """Execute a REST-backed crawl in the background.

    With `resume=True` and a saved state file present, the crawl continues
    from the persisted BFS queue/visited set/graph instead of the seeds.
    A registered stop event halts the crawl at the next round boundary.
    """
    record = _jobs[job_id]
    record.status = JobStatus.RUNNING
    crawler = None

    try:
        from .crawler import GitHubCrawler
        from .github_client import GitHubClient, resolve_cache_dir

        tokens_raw = os.environ.get("GITHUB_TOKEN", "")
        tokens = [t.strip() for t in tokens_raw.split(",") if t.strip()]
        if not tokens:
            record.status = JobStatus.FAILED
            record.detail = "GITHUB_TOKEN environment variable is not set"
            return

        jsonld_dir: Optional[Path] = None
        jsonld_zip_path: Optional[Path] = None
        if body.gimie_repos and body.gimie_store_jsonld:
            jsonld_dir = _snapshot_dir(job_id) / "jsonld"
            jsonld_dir.mkdir(parents=True, exist_ok=True)
            jsonld_zip_path = _snapshot_dir(job_id) / "jsonld.zip"

        client = GitHubClient(tokens=tokens, cache_dir=resolve_cache_dir())
        crawler = GitHubCrawler(
            client=client,
            max_rounds=body.max_rounds,
            state_file=_state_path(job_id),
            batch_size=body.batch_size,
            crawl_dependencies=body.crawl_dependencies,
            crawl_dependents=body.crawl_dependents,
            crawl_issues=body.crawl_issues,
            crawl_prs=body.crawl_prs,
            issue_max=body.issue_max,
            pr_max=body.pr_max,
            min_stars=body.min_stars,
            max_dependents=body.max_dependents,
            epfl_entities=set(body.epfl_entities),
            gimie_repos=body.gimie_repos,
            gimie_api_base=body.gimie_api_base,
            gimie_store_jsonld_dir=jsonld_dir,
            gimie_skip_existing_jsonld=body.gimie_skip_existing_jsonld,
        )
        crawler.stop_event = _stop_events.get(job_id)
        _seed_or_resume(crawler, body, job_id, resume)
        _install_snapshotting(crawler, record, job_id)
        crawler.crawl(show_progress=False)

        if body.gimie_repos and jsonld_dir is not None:
            record.jsonld_dir = str(jsonld_dir)
            record.jsonld_zip_path = str(jsonld_zip_path) if jsonld_zip_path else None
            if body.gimie_archive_on_download and jsonld_zip_path:
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
        _finalize_job(job_id, record, crawler)
    except Exception as exc:
        logger.exception("Crawl job %s failed", job_id)
        record.status = JobStatus.FAILED
        record.detail = str(exc)
        # Persist whatever rounds completed before the failure so the
        # partial graph is still recoverable via ?partial=true.
        if crawler is not None:
            _write_snapshot(job_id, crawler.graph, JobStatus.FAILED, record.rounds_completed)
    finally:
        _stop_events.pop(job_id, None)


def _run_crawl_graphql(job_id: str, body: "CrawlRequest", resume: bool = False) -> None:
    """Execute a GraphQL-backed crawl in the background.

    Reuses GitHubCrawler. The GraphQL client returns cached-shape dicts,
    so the crawler routes through its cached-path code. SBOM dependencies
    and "Used by" dependents still go via REST (existing helpers). Stop
    and resume behave exactly as for the REST-backed crawl.
    """
    record = _jobs[job_id]
    record.status = JobStatus.RUNNING
    crawler = None

    try:
        from .crawler import GitHubCrawler
        from .github_client import resolve_cache_dir
        from .graphql_client import GitHubGraphQLClient

        tokens_raw = os.environ.get("GITHUB_TOKEN", "")
        tokens = [t.strip() for t in tokens_raw.split(",") if t.strip()]
        if not tokens:
            record.status = JobStatus.FAILED
            record.detail = "GITHUB_TOKEN environment variable is not set"
            return

        client = GitHubGraphQLClient(
            tokens=tokens,
            cache_dir=resolve_cache_dir(),
            crawl_issues=body.crawl_issues,
            crawl_prs=body.crawl_prs,
            issue_max=body.issue_max,
            pr_max=body.pr_max,
        )
        crawler = GitHubCrawler(
            client=client,
            max_rounds=body.max_rounds,
            state_file=_state_path(job_id),
            batch_size=body.batch_size,
            crawl_dependencies=body.crawl_dependencies,
            crawl_dependents=body.crawl_dependents,
            crawl_issues=body.crawl_issues,
            crawl_prs=body.crawl_prs,
            issue_max=body.issue_max,
            pr_max=body.pr_max,
            min_stars=body.min_stars,
            max_dependents=body.max_dependents,
            epfl_entities=set(body.epfl_entities),
        )
        crawler.stop_event = _stop_events.get(job_id)
        _seed_or_resume(crawler, body, job_id, resume)
        _install_snapshotting(crawler, record, job_id)
        crawler.crawl(show_progress=False)
        _finalize_job(job_id, record, crawler)
    except Exception as exc:
        logger.exception("GraphQL crawl job %s failed", job_id)
        record.status = JobStatus.FAILED
        record.detail = str(exc)
        if crawler is not None:
            _write_snapshot(job_id, crawler.graph, JobStatus.FAILED, record.rounds_completed)
    finally:
        _stop_events.pop(job_id, None)


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
    # The stop event is created here (not in the background task) so a
    # /stop call that races ahead of the task starting still finds it.
    _stop_events[job_id] = threading.Event()
    _persist_request(job_id, body, mode="rest")
    background_tasks.add_task(_run_crawl, job_id, body)
    return CrawlJobResponse(job_id=job_id, status=JobStatus.PENDING)


@app.post(
    "/api/v1/crawl/graphql",
    response_model=CrawlJobResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def start_crawl_graphql(
    body: CrawlRequest,
    background_tasks: BackgroundTasks,
    _token: str = Depends(verify_token),
) -> CrawlJobResponse:
    """Start a crawl using the GraphQL-backed client.

    Same request body as `/api/v1/crawl`. Uses GraphQL for user/org/repo
    metadata, follows/stars/watching, teams, and issue/PR activity.
    Contributors, SBOM dependencies, and the "Used by" dependents graph
    still fall back to REST (no GraphQL coverage). Gimie hybrid mode is
    not supported here — use `/api/v1/crawl` for that.
    """
    job_id = str(uuid.uuid4())
    _jobs[job_id] = _JobRecord()
    _stop_events[job_id] = threading.Event()
    _persist_request(job_id, body, mode="graphql")
    background_tasks.add_task(_run_crawl_graphql, job_id, body)
    return CrawlJobResponse(job_id=job_id, status=JobStatus.PENDING)


@app.post("/api/v1/crawl/{job_id}/stop", response_model=CrawlJobResponse)
def stop_crawl(
    job_id: str,
    _token: str = Depends(verify_token),
) -> CrawlJobResponse:
    """Request a cooperative stop of a running crawl.

    Returns immediately. The crawl halts at the next BFS round boundary —
    the in-flight round drains first, so the graph stays round-consistent —
    and the job's status then flips to `stopped`. A stopped job's state is
    persisted and can be continued with `POST .../resume`.
    """
    record = _jobs.get(job_id)
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    if record.status not in (JobStatus.PENDING, JobStatus.RUNNING):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Job is not stoppable (current status: {record.status.value})",
        )
    event = _stop_events.get(job_id)
    if event is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Job has no active stop signal",
        )
    event.set()
    return CrawlJobResponse(
        job_id=job_id,
        status=record.status,
        detail="Stop requested; the crawl will halt after the current round drains.",
    )


@app.post(
    "/api/v1/crawl/{job_id}/resume",
    response_model=CrawlJobResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def resume_crawl(
    job_id: str,
    background_tasks: BackgroundTasks,
    _token: str = Depends(verify_token),
) -> CrawlJobResponse:
    """Resume a stopped/failed crawl from its persisted BFS state.

    Continues from the saved queue + visited set + graph rather than the
    seeds. Works even when the in-memory job record was lost (container
    restart), as long as the job's `state.json` + `request.json` are on
    disk. The crawl is re-dispatched on whichever client (REST/GraphQL)
    the original job used.
    """
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
    record = _jobs.get(job_id)
    if record is not None and record.status in (JobStatus.PENDING, JobStatus.RUNNING):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Job is already active (status: {record.status.value})",
        )

    try:
        body = CrawlRequest(**saved["request"])
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Persisted request is invalid: {exc}",
        )

    mode = saved.get("mode", "rest")
    task = _run_crawl_graphql if mode == "graphql" else _run_crawl
    _jobs[job_id] = _JobRecord()
    _stop_events[job_id] = threading.Event()
    background_tasks.add_task(task, job_id, body, True)  # resume=True
    return CrawlJobResponse(
        job_id=job_id,
        status=JobStatus.PENDING,
        detail="Resuming crawl from saved state.",
    )


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
        rounds_completed=record.rounds_completed,
    )
    if record.graph:
        resp.users = len(record.graph.users)
        resp.orgs = len(record.graph.orgs)
        resp.repos = len(record.graph.repos)
    return resp


@app.get("/api/v1/graph/{job_id}", response_model=GraphResponse)
def get_graph(
    job_id: str,
    partial: bool = False,
    _token: str = Depends(verify_token),
) -> GraphResponse:
    """Return graph data for a crawl job.

    Default (strict) behavior is unchanged: only a COMPLETED job in memory
    is served. Pass `?partial=true` to also read a partial graph — from the
    in-memory record of a still-RUNNING/FAILED job, or, if the job record
    is gone (container restart), from the last per-round disk snapshot.
    The `partial` field in the response flags non-final output.
    """
    record = _jobs.get(job_id)

    # Strict path: completed job held in memory — unchanged semantics.
    if record is not None and record.status == JobStatus.COMPLETED and record.graph is not None:
        return GraphResponse(
            job_id=job_id,
            graph=record.graph.model_dump(),
            partial=False,
            status=record.status,
            rounds_completed=record.rounds_completed,
        )

    if not partial:
        # Keep the strict 409/404 contract for callers that did not opt in.
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

    # Partial read: prefer the on-disk snapshot (always round-consistent;
    # written atomically between rounds) over the in-memory graph, which a
    # concurrent round could be mutating.
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

    # No snapshot on disk. Fall back to the in-memory graph if present
    # (e.g. a job that failed before its first round finished).
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
