"""FastAPI v2 router — unified multi-platform endpoints.

Exposes a small, version-stable surface over the multi-platform crawler:

- ``GET  /api/v2/health`` — liveness, same shape as v1.
- ``GET  /api/v2/platforms`` — configured hosts + token presence.
- ``POST /api/v2/crawl`` — start a crawl that accepts github.com **and**
  self-hosted GitLab seeds in the same request body shape as v1.
- ``GET  /api/v2/graph/{job_id}`` — discovered graph for a job (same DTO as v1).
- ``GET  /api/v2/nodes`` — filtered node listing across every completed job.

The v1 router stays the source of truth for the github-only legacy flow.
v2 is intentionally additive — it reuses the v1 in-memory job store
(:data:`open_pulse_crawler.api.deps._jobs`) and on-disk snapshots so
``/api/v2/graph/{job_id}`` and ``/api/v2/nodes`` see jobs submitted via
either router.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
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
from ..config import enabled_instances, resolve_tokens
from .deps import (
    CrawlJobResponse,
    CrawlRequest,
    GraphResponse,
    HealthResponse,
    JobStatus,
    _JobRecord,
    _api_cache_dir,
    _install_snapshotting,
    _jobs,
    _persist_request,
    _read_snapshot,
    _seed_or_resume,
    _state_path,
    _write_snapshot,
)

logger = logging.getLogger(__name__)


router = APIRouter()


# ---------------------------------------------------------------------------
# Response models specific to v2
# ---------------------------------------------------------------------------


class PlatformInfo(BaseModel):
    """One row in ``GET /api/v2/platforms``."""

    host: str = Field(description="Configured platform host, e.g. ``gitlab.epfl.ch``.")
    tokens: int = Field(description="Number of tokens resolved for this host.")
    ok: bool = Field(
        description=(
            "Whether the host has at least one token configured. Does not "
            "verify the token works — that is ``crawler doctor``'s job."
        )
    )


class PlatformsResponse(BaseModel):
    """Listing of every host the crawler is configured to talk to."""

    platforms: List[PlatformInfo] = Field(description="Hosts in `CRAWLER_PLATFORMS` order.")


class NodeListResponse(BaseModel):
    """A filtered listing of nodes across every completed job's graph."""

    nodes: List[Dict[str, Any]] = Field(
        description="Node dicts (each is the model's ``model_dump()``)."
    )
    count: int = Field(description="Number of nodes returned after filtering.")


# ---------------------------------------------------------------------------
# /health
# ---------------------------------------------------------------------------


@router.get(
    "/health",
    response_model=HealthResponse,
    tags=["Health"],
    summary="Liveness check (v2)",
)
def health() -> HealthResponse:
    """Unauthenticated liveness probe — same shape as ``/api/v1/health``."""
    return HealthResponse(status="ok", version=__version__)


# ---------------------------------------------------------------------------
# /platforms
# ---------------------------------------------------------------------------


@router.get(
    "/platforms",
    response_model=PlatformsResponse,
    tags=["Platforms"],
    summary="List configured platforms",
)
def list_platforms() -> PlatformsResponse:
    """Return the list of configured platform hosts and their token presence.

    Reads :func:`open_pulse_crawler.config.enabled_instances` for the host
    list and :func:`open_pulse_crawler.config.resolve_tokens` for each host.
    ``ok`` only checks for token presence — it does not call the upstream
    API. Use ``crawler doctor`` for an end-to-end probe.
    """
    rows: List[PlatformInfo] = []
    for host in enabled_instances():
        tokens = resolve_tokens(host)
        rows.append(PlatformInfo(host=host, tokens=len(tokens), ok=len(tokens) > 0))
    return PlatformsResponse(platforms=rows)


# ---------------------------------------------------------------------------
# /crawl — multi-platform background crawl
# ---------------------------------------------------------------------------


def _build_registry_from_env() -> "object":
    """Build a :class:`PlatformRegistry` for every host in CRAWLER_PLATFORMS.

    Hosts without resolved tokens are skipped (a GitLab/GitHub client with
    zero tokens raises in its constructor). Imports the adapters lazily so
    the module's import side effects stay cheap for ``/health`` traffic.
    """
    from ..platforms import PlatformRegistry
    from ..platforms.github import GitHubClient, resolve_cache_dir
    from ..platforms.github.adapter import GitHubAdapter
    from ..platforms.gitlab import GitLabClient
    from ..platforms.gitlab.adapter import GitLabAdapter

    registry = PlatformRegistry()
    cache_dir = resolve_cache_dir(default=_api_cache_dir())

    for host in enabled_instances():
        tokens = resolve_tokens(host)
        if not tokens:
            logger.warning("Skipping %s: no tokens configured", host)
            continue
        if host == "github.com":
            client = GitHubClient(tokens=tokens, cache_dir=cache_dir)
            registry.register(GitHubAdapter(client, instance_host="github.com"))
        else:
            # Treat every non-github.com host as a GitLab-flavoured instance
            # for now; Renku / other forge support lands in later tasks.
            client = GitLabClient(host=host, tokens=tokens)
            registry.register(GitLabAdapter(client, instance_host=host))
    return registry


def _run_crawl_v2(
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
    """Run a multi-platform crawl in the background.

    Builds a :class:`PlatformRegistry` from every host in ``CRAWLER_PLATFORMS``
    that has a token, then hands the registry to ``GitHubCrawler`` in
    pure-registry mode (no ``client=`` argument). Mirrors :func:`_run_crawl`
    in terms of job-record bookkeeping + per-round snapshotting.
    """
    record = _jobs[job_id]
    record.status = JobStatus.RUNNING
    record.started_at = datetime.now(timezone.utc)

    try:
        from ..crawler import GitHubCrawler

        try:
            registry = _build_registry_from_env()
        except Exception as exc:
            record.status = JobStatus.FAILED
            record.detail = f"Failed to build platform registry: {exc}"
            record.completed_at = datetime.now(timezone.utc)
            return

        if not registry.hosts():
            record.status = JobStatus.FAILED
            record.detail = (
                "No platforms have tokens configured. Set "
                "CRAWLER_TOKEN__<HOST> (or CRAWLER_TOKEN_POOL__<HOST>) for "
                "each entry in CRAWLER_PLATFORMS."
            )
            record.completed_at = datetime.now(timezone.utc)
            return

        crawler = GitHubCrawler(
            registry=registry,
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
        logger.exception("v2 crawl job %s failed", job_id)
        record.status = JobStatus.FAILED
        record.detail = str(exc)
        record.completed_at = datetime.now(timezone.utc)
        if record.graph is not None:
            _write_snapshot(
                job_id, record.graph, JobStatus.FAILED, record.rounds_completed
            )


@router.post(
    "/crawl",
    response_model=CrawlJobResponse,
    status_code=status.HTTP_202_ACCEPTED,
    tags=["Crawl"],
    summary="Start a multi-platform crawl",
)
def start_crawl_v2(
    background_tasks: BackgroundTasks,
    body: CrawlRequest = Body(...),
    _token: str = Depends(verify_token),
) -> CrawlJobResponse:
    """Submit a crawl whose seeds may target any configured platform host.

    Same request body as ``POST /api/v1/crawl``. Background-task wiring
    uses a multi-host :class:`PlatformRegistry` instead of a single
    ``GitHubClient``.
    """
    job_id = str(uuid.uuid4())
    _jobs[job_id] = _JobRecord()
    _persist_request(job_id, body, mode="v2")
    background_tasks.add_task(
        _run_crawl_v2,
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


# ---------------------------------------------------------------------------
# /graph/{job_id} — same DTO as v1 for now
# ---------------------------------------------------------------------------


@router.get(
    "/graph/{job_id}",
    response_model=GraphResponse,
    tags=["Graph"],
    summary="Fetch the graph (v2)",
)
def get_graph_v2(
    job_id: str = PathParam(
        description="Job identifier returned by `POST /api/v2/crawl` or `/api/v1/crawl`.",
    ),
    partial: bool = Query(
        default=False,
        description=(
            "When false (default), only a completed job in memory is served. "
            "When true, a partial graph is recovered from the on-disk snapshot."
        ),
    ),
    _token: str = Depends(verify_token),
) -> GraphResponse:
    """Return graph data for a crawl job — same shape as ``/api/v1/graph``.

    The shape already reflects the v3 :class:`GraphData` layout (URL-keyed
    dicts of subkind-tagged models), which is what v2 promises.
    """
    record = _jobs.get(job_id)

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


# ---------------------------------------------------------------------------
# /nodes — filtered listing across every completed job
# ---------------------------------------------------------------------------


def _iter_completed_graphs():
    """Yield ``GraphData`` instances from every job currently in COMPLETED."""
    for record in _jobs.values():
        if record.status != JobStatus.COMPLETED or record.graph is None:
            continue
        yield record.graph


def _matches_filters(
    node: Any,
    subkind: Optional[str],
    platform: Optional[str],
    instance: Optional[str],
) -> bool:
    """Return True iff ``node`` passes every non-None filter (AND-combined)."""
    if subkind is not None and getattr(node, "subkind", None) != subkind:
        return False
    if platform is not None and getattr(node, "platform", None) != platform:
        return False
    if instance is not None:
        # ``instance`` filters by URL host — same definition as
        # GraphData.by_instance.
        from urllib.parse import urlparse

        url = getattr(node, "url", "") or ""
        if urlparse(url).netloc.lower() != instance.lower():
            return False
    return True


@router.get(
    "/nodes",
    response_model=NodeListResponse,
    tags=["Graph"],
    summary="List nodes across every completed job",
)
def list_nodes(
    subkind: Optional[str] = Query(
        default=None,
        description="Filter by ``subkind`` (e.g. ``GitLabProject``).",
    ),
    platform: Optional[str] = Query(
        default=None,
        description='Filter by ``platform`` (e.g. ``github`` or ``gitlab``).',
    ),
    instance: Optional[str] = Query(
        default=None,
        description="Filter by URL host (e.g. ``gitlab.epfl.ch``).",
    ),
    _token: str = Depends(verify_token),
) -> NodeListResponse:
    """Return every node across every completed job's graph, AND-filtered.

    Filters are optional — omitting all three returns the full union.
    The result dict is the node model's ``model_dump()``.
    """
    out: List[Dict[str, Any]] = []
    for graph in _iter_completed_graphs():
        for collection in (graph.users, graph.orgs, graph.repos, graph.teams):
            for node in collection.values():
                if _matches_filters(node, subkind, platform, instance):
                    out.append(node.model_dump())
    return NodeListResponse(nodes=out, count=len(out))


__all__ = ["router"]
