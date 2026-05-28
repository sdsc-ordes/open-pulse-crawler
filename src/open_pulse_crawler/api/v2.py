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


# Named examples for ``POST /api/v2/crawl``. Surfaced in Swagger UI as a
# dropdown so operators can try the multi-platform shape without typing
# a request body from scratch. The bovel / vermeul examples target real
# EPFL / ETHZ GitLab profiles — they're the smallest seeds that exercise
# the per-host adapter dispatch end-to-end.
_CRAWL_V2_REQUEST_EXAMPLES = {
    "epfl_user_bovel": {
        "summary": "EPFL GitLab user (Matthieu Bovel)",
        "description": (
            "Crawl one EPFL GitLab user profile, two BFS rounds. The "
            "`/users/<name>` dashboard URL is normalized to the canonical "
            "`/<name>` profile form by the GitLab adapter. Requires "
            "`CRAWLER_TOKEN__GITLAB_EPFL_CH` (or anonymous public-data "
            "reads if the instance allows them)."
        ),
        "value": {
            "seeds": ["https://gitlab.epfl.ch/users/bovel"],
            "max_rounds": 2,
        },
    },
    "ethz_user_vermeul": {
        "summary": "ETHZ GitLab user (vermeul)",
        "description": (
            "Crawl one ETHZ GitLab user profile, two BFS rounds. Uses "
            "`CRAWLER_TOKEN__GITLAB_ETHZ_CH` when set; falls back to "
            "anonymous reads — fine for public profile metadata, but "
            "expansion endpoints (user projects/starred/contributed) "
            "return 403 without `read_api` scope and are skipped."
        ),
        "value": {
            "seeds": ["https://gitlab.ethz.ch/vermeul"],
            "max_rounds": 2,
        },
    },
    "mixed_epfl_ethz": {
        "summary": "Cross-instance GitLab crawl",
        "description": (
            "Seeds from two self-hosted instances in one job. Each "
            "URL routes to its host's adapter via `PlatformRegistry`. "
            "Configure both `CRAWLER_TOKEN__GITLAB_EPFL_CH` and "
            "`CRAWLER_TOKEN__GITLAB_ETHZ_CH`, and enable both hosts via "
            "`CRAWLER_PLATFORMS=gitlab.epfl.ch,gitlab.ethz.ch`."
        ),
        "value": {
            "seeds": [
                "https://gitlab.epfl.ch/users/bovel",
                "https://gitlab.ethz.ch/vermeul",
            ],
            "max_rounds": 2,
        },
    },
    "github_plus_epfl": {
        "summary": "GitHub + GitLab in one crawl",
        "description": (
            "Mix a github.com seed with an EPFL GitLab seed. The crawler's "
            "dual-path dispatch routes the github.com URL through the "
            "legacy `_process_*` helpers and the gitlab.epfl.ch URL "
            "through the `GitLabAdapter`. Set "
            "`CRAWLER_PLATFORMS=github.com,gitlab.epfl.ch` and both "
            "corresponding tokens."
        ),
        "value": {
            "seeds": [
                "sdsc-ordes/open-pulse-crawler",
                "https://gitlab.epfl.ch/users/bovel",
            ],
            "max_rounds": 2,
            "max_contributors": 100,
        },
    },
    "renku_hslu_group": {
        "summary": "Renku group (HSLU Predictive Modeling)",
        "description": (
            "Crawl an `gitlab.renkulab.io` group. The seed is the group "
            "page; round 1 discovers its owned projects (2 in this case) "
            "and round 2 fans out to their forks. Member listing requires "
            "auth and is gracefully skipped when anonymous — the public "
            "project edges still land. No token required."
        ),
        "value": {
            "seeds": ["https://gitlab.renkulab.io/HSLU-Predictive-Modeling"],
            "max_rounds": 2,
        },
    },
    "renku_hslu_project": {
        "summary": "Renku project + fork tree (HSLU course)",
        "description": (
            "Crawl a single Renku project and follow its fork tree. The "
            "HSLU Predictive Modeling course's main repo has ~74 student "
            "forks, all discovered in round 1 via "
            "`/projects/:id/forks`. Useful demo of fork-graph crawling on "
            "a self-hosted GitLab. Anonymous reads work."
        ),
        "value": {
            "seeds": [
                "https://gitlab.renkulab.io/HSLU-Predictive-Modeling/hslu-predictive-modeling",
            ],
            "max_rounds": 2,
        },
    },
    "zenodo_community_escape2020": {
        "summary": "Zenodo community (ESCAPE OSSR) — software-rich",
        "description": (
            "Crawl the ESCAPE Open Science Software Repository, a Zenodo "
            "community where ~14/15 records carry `related_identifiers` to "
            "their source repos on GitHub / GitLab. Anonymous-friendly "
            "(no CRAWLER_TOKEN__ZENODO_ORG needed). Round 0 fetches the "
            "community; round 1 walks `contains` edges to its 56 records. "
            "Pair with `CRAWLER_PLATFORMS=zenodo.org,github.com` to follow "
            "`related_to.isDerivedFrom` / `isDocumentedBy` edges into the "
            "actual code (Gammapy, R3BRoot, CTLearn, …)."
        ),
        "value": {
            "seeds": ["https://zenodo.org/communities/escape2020"],
            "max_rounds": 2,
        },
    },
    "zenodo_community_eosc": {
        "summary": "Zenodo community (EOSC Association) — lighter demo",
        "description": (
            "Smaller smoke-test community: ~72 records, fewer outgoing "
            "links than escape2020 but still useful for verifying the "
            "round-1 `contains` fan-out. Used by the integration test."
        ),
        "value": {
            "seeds": ["https://zenodo.org/communities/eosc"],
            "max_rounds": 2,
        },
    },
    "zenodo_record_doi_url": {
        "summary": "Zenodo record via DOI URL",
        "description": (
            "DOI URLs (`https://doi.org/10.5281/zenodo.<id>`) are rewritten "
            "to canonical Zenodo URLs by the adapter. Useful when copying a "
            "DOI from a citation."
        ),
        "value": {
            "seeds": ["https://doi.org/10.5281/zenodo.7234562"],
            "max_rounds": 2,
        },
    },
    "zenodo_record_canonical": {
        "summary": "Zenodo record via canonical URL",
        "description": (
            "Direct seeding with the platform URL. When the record is a "
            "specific version, the adapter follows its concept DOI and "
            "stores the result under the concept URL (versions collapse "
            "into the concept node's `versions` field)."
        ),
        "value": {
            "seeds": ["https://zenodo.org/records/7234562"],
            "max_rounds": 2,
        },
    },
    "cross_platform_zenodo_github_gammapy": {
        "summary": "Zenodo record → GitHub repo via related_to edges (Gammapy)",
        "description": (
            "Gammapy is a Python toolbox for gamma-ray astronomy. Its "
            "Zenodo deposit links to https://github.com/gammapy/gammapy "
            "via `related_to.isDerivedFrom`. With both adapters registered "
            "(`CRAWLER_PLATFORMS=zenodo.org,github.com`), round 0 fetches "
            "the Zenodo record; round 1 follows the cross-platform edge "
            "to the GitHub org + repo and crawls them in place. Real-data "
            "example exercising the full link-following behavior."
        ),
        "value": {
            "seeds": ["https://zenodo.org/records/20432079"],
            "max_rounds": 2,
        },
    },
    "cross_platform_escape_full": {
        "summary": "Zenodo community → GitHub orgs (ESCAPE OSSR)",
        "description": (
            "End-to-end demo of multi-platform crawling. Seed = the ESCAPE "
            "OSSR community on Zenodo. Round 0: fetches the community. "
            "Round 1: walks `contains` edges to its records, then follows "
            "each record's `related_to.isDerivedFrom` / `isDocumentedBy` "
            "to GitHub orgs (gammapy, FairRootGroup, R3BRootGroup, "
            "ctlearn-project, cds-astro, …) and Zenodo sibling communities "
            "(astronomy-general, oscars, gammalearn, …). Requires both "
            "`CRAWLER_PLATFORMS=zenodo.org,github.com` and a GitHub token. "
            "Caps round-1 fan-out at ~28 graph nodes for a 2-round crawl."
        ),
        "value": {
            "seeds": ["https://zenodo.org/communities/escape2020"],
            "max_rounds": 2,
        },
    },
}


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
    body: CrawlRequest = Body(..., openapi_examples=_CRAWL_V2_REQUEST_EXAMPLES),
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
