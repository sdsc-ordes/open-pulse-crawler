"""FastAPI v1 router for the Open Pulse Crawler REST API."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Body,
    Depends,
    HTTPException,
    Query,
    status,
)
from fastapi import Path as PathParam  # aliased: `Path` is pathlib.Path here
from pydantic import BaseModel, Field

from .. import __version__
from ..auth import verify_token
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
    _jobs,
    _load_persisted_request,
    _persist_request,
    _read_snapshot,
    _run_crawl,
    _run_crawl_graphql,
    _state_path,
)

logger = logging.getLogger(__name__)


router = APIRouter()


# States the BFS loop has stopped or never started running. Used by the
# DELETE endpoint so we don't drop a still-active job out from under itself.
_TERMINAL_STATES = {
    JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED, JobStatus.PENDING,
}


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
    "epfl_with_contributor_cap": {
        "summary": "Cap contributors per repo",
        "description": (
            "Crawl from an EPFL-flavoured seed list, taking up to 200 "
            "contributors per repo. A repo with more contributors than the "
            "cap is truncated to its top 200 — it still contributes, it is "
            "never skipped. Raise or lower the number to trade BFS breadth "
            "against crawl size."
        ),
        "value": {
            "seeds": ["epfl", "dslab-epfl", "sdsc-ordes/gimie"],
            "max_rounds": 2,
            "max_contributors": 200,
        },
    },
}


# Reusable error-response documentation for the OpenAPI schema. Spread into a
# route's `responses=` so Swagger shows the failure cases, not just 200/202.
_RESP_AUTH = {
    401: {"description": "Missing or invalid Bearer token"},
    403: {"description": "Authorization header missing or not a Bearer token"},
}
_RESP_JOB_NOT_FOUND = {404: {"description": "No job exists with that `job_id`"}}
_RESP_JOB_CONFLICT = {
    409: {"description": "The job's current status does not allow this action"}
}


@router.get(
    "/health",
    response_model=HealthResponse,
    tags=["Health"],
    summary="Liveness check",
)
def health() -> HealthResponse:
    """Unauthenticated health check — returns `ok` and the running version.

    Use this for container/orchestrator probes. It needs no Bearer token.
    """
    return HealthResponse(status="ok", version=__version__)


@router.post(
    "/crawl",
    response_model=CrawlJobResponse,
    status_code=status.HTTP_202_ACCEPTED,
    tags=["Crawl"],
    summary="Start a crawl (REST)",
    responses={**_RESP_AUTH},
)
def start_crawl(
    background_tasks: BackgroundTasks,
    body: CrawlRequest = Body(..., openapi_examples=_CRAWL_REQUEST_EXAMPLES),
    _token: str = Depends(verify_token),
) -> CrawlJobResponse:
    """Start a new crawl job. It runs in the background and returns a
    `job_id` straight away — poll `GET /api/v1/crawl/{job_id}` for progress
    and fetch the result from `GET /api/v1/graph/{job_id}` once `completed`.

    **Tip:** in *Try it out*, open the **Examples** dropdown on the request
    body to prefill a ready-to-send payload (single repo, org crawl, repo
    with dependents, or a mega-project-skip crawl).
    """
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


@router.post(
    "/crawl/graphql",
    response_model=CrawlJobResponse,
    status_code=status.HTTP_202_ACCEPTED,
    tags=["Crawl"],
    summary="Start a crawl (GraphQL)",
    responses={**_RESP_AUTH},
)
def start_crawl_graphql(
    background_tasks: BackgroundTasks,
    body: CrawlRequest = Body(..., openapi_examples=_CRAWL_REQUEST_EXAMPLES),
    _token: str = Depends(verify_token),
) -> CrawlJobResponse:
    """Start a crawl using the **GraphQL-backed** client.

    Same request body and response as `POST /api/v1/crawl`, but fetches user
    / org / repo metadata in one GraphQL query per entity instead of dozens
    of REST calls — far cheaper on rate limits, especially with
    `crawl_issues` / `crawl_prs` enabled.

    GraphQL covers metadata, follows / stars / watching, teams, and issue/PR
    activity. Contributors, SBOM dependencies, and the "Used by" dependents
    graph still fall back to REST. Gimie hybrid mode is **not** available on
    this endpoint — use `POST /api/v1/crawl` for that. Org-level fields
    require a token with the `read:org` scope.
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


@router.get(
    "/crawl/{job_id}",
    response_model=CrawlResultResponse,
    tags=["Crawl"],
    summary="Get job status & progress",
    responses={**_RESP_AUTH, **_RESP_JOB_NOT_FOUND},
)
def get_crawl_status(
    job_id: str = PathParam(
        description="Job identifier returned by `POST /api/v1/crawl`.",
        examples=["d290f1ee-6c54-4b01-90e6-d701748f0851"],
    ),
    _token: str = Depends(verify_token),
) -> CrawlResultResponse:
    """Status, summary counts, and — for running jobs — live BFS progress:
    current round, nodes processed, queue size, and a best-effort ETA.

    Poll this while a crawl runs; switch to `GET /api/v1/graph/{job_id}` once
    the status is `completed`.
    """
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


@router.get(
    "/jobs",
    response_model=JobsListResponse,
    tags=["Crawl"],
    summary="List all jobs",
    responses={**_RESP_AUTH},
)
def list_jobs(
    status_filter: Optional[JobStatus] = Query(
        default=None,
        description="Return only jobs in this status (e.g. `completed`).",
        examples=["completed"],
    ),
    _token: str = Depends(verify_token),
) -> JobsListResponse:
    """List every job in the in-memory registry, newest first.

    Pass `?status_filter=` to narrow to one status — for example
    `?status_filter=completed` to find jobs whose graph is ready to fetch.
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


@router.get(
    "/graph/{job_id}",
    response_model=GraphResponse,
    tags=["Graph"],
    summary="Fetch the graph",
    responses={**_RESP_AUTH, **_RESP_JOB_NOT_FOUND, **_RESP_JOB_CONFLICT},
)
def get_graph(
    job_id: str = PathParam(
        description="Job identifier returned by `POST /api/v1/crawl`.",
        examples=["d290f1ee-6c54-4b01-90e6-d701748f0851"],
    ),
    partial: bool = Query(
        default=False,
        description=(
            "When false (default), only a `completed` job is served — "
            "otherwise `409`. When true, a partial graph is returned from a "
            "running / cancelled / failed job, or recovered from the last "
            "on-disk snapshot if the in-memory record was lost."
        ),
    ),
    _token: str = Depends(verify_token),
) -> GraphResponse:
    """Return graph data for a crawl job.

    **Strict (default):** only a `completed` job held in memory is served;
    anything else returns `409`.

    **`?partial=true`:** also serves a partial graph — from a running /
    cancelled / failed job, or, if the in-memory record is gone (container
    restart), from the last per-round disk snapshot keyed by `job_id`. The
    `partial` field in the response flags non-final output.
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
    """Result of a lifecycle action (pause / resume / cancel / delete)."""

    job_id: str = Field(description="Job identifier.")
    status: JobStatus = Field(
        description="Job status after the action was applied or requested."
    )
    detail: Optional[str] = Field(
        default=None, description="What the action did, in human-readable form."
    )

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "job_id": "d290f1ee-6c54-4b01-90e6-d701748f0851",
                    "status": "paused",
                    "detail": "paused",
                }
            ]
        }
    }


def _job_or_404(job_id: str) -> _JobRecord:
    record = _jobs.get(job_id)
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    return record


@router.post(
    "/crawl/{job_id}/pause",
    response_model=JobActionResponse,
    tags=["Job lifecycle"],
    summary="Pause a running job",
    responses={**_RESP_AUTH, **_RESP_JOB_NOT_FOUND, **_RESP_JOB_CONFLICT},
)
def pause_crawl(
    job_id: str = PathParam(
        description="Job identifier of a `running` job.",
        examples=["d290f1ee-6c54-4b01-90e6-d701748f0851"],
    ),
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


@router.post(
    "/crawl/{job_id}/resume",
    response_model=JobActionResponse,
    tags=["Job lifecycle"],
    summary="Resume a paused, cancelled, or failed job",
    responses={**_RESP_AUTH, **_RESP_JOB_NOT_FOUND, **_RESP_JOB_CONFLICT},
)
def resume_crawl(
    background_tasks: BackgroundTasks,
    job_id: str = PathParam(
        description="Job identifier of a `paused`, `cancelled`, or `failed` job.",
        examples=["d290f1ee-6c54-4b01-90e6-d701748f0851"],
    ),
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


@router.post(
    "/crawl/{job_id}/cancel",
    response_model=JobActionResponse,
    tags=["Job lifecycle"],
    summary="Cancel a job",
    responses={**_RESP_AUTH, **_RESP_JOB_NOT_FOUND, **_RESP_JOB_CONFLICT},
)
def cancel_crawl(
    job_id: str = PathParam(
        description="Job identifier of a `pending`, `running`, or `paused` job.",
        examples=["d290f1ee-6c54-4b01-90e6-d701748f0851"],
    ),
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


@router.delete(
    "/crawl/{job_id}",
    response_model=JobActionResponse,
    tags=["Job lifecycle"],
    summary="Delete a terminal job",
    responses={**_RESP_AUTH, **_RESP_JOB_NOT_FOUND, **_RESP_JOB_CONFLICT},
)
def delete_crawl(
    job_id: str = PathParam(
        description="Job identifier of a terminal job (completed / failed / "
        "cancelled / pending).",
        examples=["d290f1ee-6c54-4b01-90e6-d701748f0851"],
    ),
    _token: str = Depends(verify_token),
) -> JobActionResponse:
    """Drop a terminal job from the in-memory registry.

    Refuses to delete a still-active job (`running` / `paused`) — cancel it
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
