"""Shared building blocks for the Open Pulse Crawler REST API.

Holds the in-memory job store, on-disk snapshot helpers, the Pydantic
request/response DTOs, and the background-task functions used by both the
``/api/v1`` router and future versioned routers. Endpoint-shaped helpers
stay alongside the routes in ``v1.py``.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

from urllib.parse import urlparse

from pydantic import BaseModel, Field

from ..models import GRAPH_SCHEMA_VERSION, GraphData
from ..node_id import canonical_url


# ---------------------------------------------------------------------------
# Graph edge-URL normalization (finding A)
# ---------------------------------------------------------------------------
#
# Node *keys* in the graph are canonical URLs, but per-node edge-list fields
# store bare platform shorthand (a login like ``"caviri"`` or an
# ``"owner/repo"`` full_name) — a compact internal representation. The CSV /
# JSON-LD exporters normalize these to URLs at write time; the REST ``/graph``
# response now does the same so consumers get a fully URL-joined graph and
# never have to re-derive the join key themselves.
#
# Normalization is **host-aware**: each shorthand entry resolves against the
# OWNING node's own host (via ``canonical_url``), which reproduces exactly how
# the target node is keyed — correct for GitHub, GitLab (incl. multi-segment
# group paths), Zenodo, Infoscience, DataCite, and HuggingFace alike. It is
# NOT GitHub-centric (the older CSV exporter's ``user_url`` defaulted to
# github.com, which would mis-resolve non-github edges).
#
# Only these curated fields are rewritten — they are the known node-reference
# edge lists. Typed dict-lists (``authors``, ``creators``, ``relations``,
# ``affiliations``, ``versions``) and non-reference scalar lists (``tags``,
# ``keywords``, ``ai_keywords``, ``subjects``, ``language``, ``domains``,
# ``doi_prefixes``, ``repository_type``) are left untouched.
_EDGE_LIST_FIELDS = frozenset({
    # login / name → user/org URL
    "followers", "following", "members", "contributors",
    "issue_authors", "pr_authors", "commenters", "pr_reviewers",
    "member_orgs",          # HuggingFaceUser → org names
    # full_name / repo_id → repo URL
    "authored_repositories", "forked_repositories",
    "starred_repositories", "watched_repositories",
    "dependents", "dependencies", "repositories",
    "used_models",          # HuggingFaceRepo (space) → model repo ids
})
# Single-value (scalar) node-reference fields.
_EDGE_SCALAR_FIELDS = frozenset({"forked_from"})


def _shorthand_to_url(value: str, host: str) -> str:
    """Map a bare shorthand ref to a canonical URL under ``host``.

    Idempotent: an entry that is already an ``http(s)://`` URL is returned
    unchanged. Anything that can't be canonicalized is returned as-is rather
    than raising, so a malformed entry never breaks the whole response.
    """
    if not isinstance(value, str) or not value:
        return value
    if value.startswith(("http://", "https://")):
        return value
    try:
        return canonical_url(host, value)
    except Exception:
        return value


def _normalize_node_edges(node_url: str, node: Dict[str, Any]) -> None:
    """In-place: rewrite a node dict's edge-list fields to canonical URLs."""
    host = ""
    own_url = node.get("url") or node_url
    if isinstance(own_url, str):
        host = urlparse(own_url).netloc
    if not host:
        return
    for field_name in _EDGE_LIST_FIELDS:
        seq = node.get(field_name)
        if isinstance(seq, list):
            node[field_name] = [_shorthand_to_url(v, host) for v in seq]
    for field_name in _EDGE_SCALAR_FIELDS:
        val = node.get(field_name)
        if isinstance(val, str) and val:
            node[field_name] = _shorthand_to_url(val, host)


def normalize_graph_edge_urls(graph: Dict[str, Any]) -> Dict[str, Any]:
    """Return ``graph`` with every node's edge-list endpoints as canonical URLs.

    Mutates and returns the same dict (it is a fresh ``model_dump()`` result
    at every call site, so in-place mutation is safe). Walks the
    ``users`` / ``orgs`` / ``repos`` / ``teams`` collections; unknown shapes
    pass through untouched.
    """
    if not isinstance(graph, dict):
        return graph
    for collection in ("users", "orgs", "repos", "teams"):
        nodes = graph.get(collection)
        if isinstance(nodes, dict):
            for key, node in nodes.items():
                if isinstance(node, dict):
                    _normalize_node_edges(key, node)
    return graph


def normalize_node_edge_urls(node: Dict[str, Any]) -> Dict[str, Any]:
    """Edge-URL-normalize a single flat node dict (for ``GET /api/v2/nodes``).

    Same host-aware rewrite as :func:`normalize_graph_edge_urls`, applied to
    one node keyed by its own ``url``. Mutates and returns ``node``.
    """
    if isinstance(node, dict):
        _normalize_node_edges(node.get("url", ""), node)
    return node


# ---------------------------------------------------------------------------
# Job-progress helpers (shared by the v1 + v2 status endpoints)
# ---------------------------------------------------------------------------


def _job_progress_snapshot(record: "_JobRecord") -> Dict[str, Any]:
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
            "Optional per-repo contributor limit. When set, at most N "
            "contributors are recorded and queued per repo — a repo with more "
            "is truncated to the top N, never skipped. Omit (the default) for "
            "no cap: every contributor is recorded and queued."
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


class CrawlJobResponse(BaseModel):
    """Acknowledgement returned when a crawl job is accepted."""

    job_id: str = Field(
        description="Unique job identifier — pass it to every follow-up call.",
        examples=["d290f1ee-6c54-4b01-90e6-d701748f0851"],
    )
    status: JobStatus = Field(
        description="Job status at submission time (always `pending`).",
        examples=["pending"],
    )
    detail: Optional[str] = Field(
        default=None, description="Human-readable note, when relevant."
    )

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "job_id": "d290f1ee-6c54-4b01-90e6-d701748f0851",
                    "status": "pending",
                    "detail": None,
                }
            ]
        }
    }


class CrawlResultResponse(BaseModel):
    """Job status, summary counts, and — while running — live progress + ETA."""

    job_id: str = Field(description="Job identifier.")
    status: JobStatus = Field(description="Current job status.")
    detail: Optional[str] = Field(
        default=None, description="Error message (failed jobs) or a status note."
    )
    users: int = Field(default=0, description="Users discovered so far.")
    orgs: int = Field(default=0, description="Organizations discovered so far.")
    repos: int = Field(default=0, description="Repositories discovered so far.")
    started_at: Optional[datetime] = Field(
        default=None, description="UTC timestamp when the crawl began."
    )
    completed_at: Optional[datetime] = Field(
        default=None, description="UTC timestamp when the crawl reached a terminal state."
    )
    current_round: Optional[int] = Field(
        default=None, description="BFS round currently in progress (running jobs only)."
    )
    nodes_processed: int = Field(
        default=0, description="Nodes visited so far across all rounds."
    )
    nodes_in_queue: int = Field(
        default=0, description="Nodes still queued for exploration."
    )
    estimated_completion_at: Optional[datetime] = Field(
        default=None,
        description=(
            "Best-effort ETA from the current processing rate. The queue grows "
            "as the crawl expands, so this drifts — treat it as a rough hint."
        ),
    )

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "summary": "Running job with live progress",
                    "value": {
                        "job_id": "d290f1ee-6c54-4b01-90e6-d701748f0851",
                        "status": "running",
                        "detail": None,
                        "users": 38,
                        "orgs": 4,
                        "repos": 121,
                        "started_at": "2026-05-22T10:00:00Z",
                        "completed_at": None,
                        "current_round": 2,
                        "nodes_processed": 163,
                        "nodes_in_queue": 540,
                        "estimated_completion_at": "2026-05-22T10:04:30Z",
                    },
                },
                {
                    "summary": "Completed job",
                    "value": {
                        "job_id": "d290f1ee-6c54-4b01-90e6-d701748f0851",
                        "status": "completed",
                        "detail": None,
                        "users": 96,
                        "orgs": 7,
                        "repos": 412,
                        "started_at": "2026-05-22T10:00:00Z",
                        "completed_at": "2026-05-22T10:05:12Z",
                        "current_round": None,
                        "nodes_processed": 515,
                        "nodes_in_queue": 0,
                        "estimated_completion_at": None,
                    },
                },
            ]
        }
    }


class JobSummary(BaseModel):
    """One row in the `GET /api/v1/jobs` listing."""

    job_id: str = Field(description="Job identifier.")
    status: JobStatus = Field(description="Current job status.")
    started_at: Optional[datetime] = Field(
        default=None, description="UTC timestamp when the crawl began."
    )
    completed_at: Optional[datetime] = Field(
        default=None, description="UTC timestamp when the crawl finished."
    )
    users: int = Field(default=0, description="Users discovered.")
    orgs: int = Field(default=0, description="Organizations discovered.")
    repos: int = Field(default=0, description="Repositories discovered.")
    detail: Optional[str] = Field(default=None, description="Status note, when relevant.")


class JobsListResponse(BaseModel):
    """All jobs in the in-memory registry, newest first."""

    jobs: List[JobSummary] = Field(description="Job summaries, ordered newest-first.")
    total: int = Field(description="Number of jobs returned.", examples=[3])


class GraphResponse(BaseModel):
    """The discovered graph plus metadata about how complete it is."""

    job_id: str = Field(description="Job identifier.")
    graph: dict = Field(
        description=(
            "The crawl graph: `users`, `orgs`, `repos`, and `teams` keyed by "
            "identifier. See the GraphData model for the per-entity shape."
        )
    )
    partial: bool = Field(
        default=False,
        description=(
            "True when this graph is not a final completed result — a running, "
            "cancelled, or failed job, or a snapshot recovered after restart."
        ),
    )
    status: Optional[JobStatus] = Field(
        default=None, description="Status the graph reflects."
    )
    rounds_completed: Optional[int] = Field(
        default=None, description="BFS rounds reflected in this graph."
    )

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "job_id": "d290f1ee-6c54-4b01-90e6-d701748f0851",
                    "graph": {
                        "users": {
                            "torvalds": {"login": "torvalds", "name": "Linus Torvalds"}
                        },
                        "orgs": {},
                        "repos": {},
                        "teams": {},
                    },
                    "partial": False,
                    "status": "completed",
                    "rounds_completed": 2,
                }
            ]
        }
    }


class HealthResponse(BaseModel):
    """Liveness probe payload."""

    status: str = Field(default="ok", description="Always `ok` when reachable.")
    version: str = Field(description="Running package version.", examples=["0.1.0"])


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


def _data_root() -> Path:
    """Root directory for API-side artifacts. Writable in the container
    (unlike the process CWD, which is root-owned)."""
    return Path(os.environ.get("OPC_DATA_DIR", "/tmp/open-pulse-crawler"))


def _api_cache_dir() -> Path:
    """Default API response cache location — under OPC_DATA_DIR so it is
    writable in the container. The repo-relative CLI default
    (`data/open-pulse-crawler/cache`) would land in a non-writable CWD."""
    return _data_root() / "cache"


def _snapshot_dir(job_id: str) -> Path:
    return _data_root() / job_id


def _state_path(job_id: str) -> Path:
    """Crawler state file (queue + visited + graph) — used by resume."""
    return _snapshot_dir(job_id) / "state.json"


def _request_path(job_id: str) -> Path:
    """Persisted crawl request — lets resume rebuild the exact config."""
    return _snapshot_dir(job_id) / "request.json"


def _write_snapshot(
    job_id: str, graph: GraphData, status_value: JobStatus, rounds_completed: int
) -> Optional[str]:
    """Atomically write the current graph + metadata to disk. Best-effort.

    The payload records ``schema_version`` at the top level so ``_read_snapshot``
    can refuse files written by an older release without re-instantiating
    GraphData (which would silently coerce login keys into a URL-keyed dict
    and produce mangled output).
    """
    try:
        job_dir = _snapshot_dir(job_id)
        job_dir.mkdir(parents=True, exist_ok=True)
        path = job_dir / "graph.snapshot.json"
        payload = {
            "schema_version": GRAPH_SCHEMA_VERSION,
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
    """Read a graph snapshot from disk. Returns the full payload dict or None.

    Snapshots written under an earlier ``schema_version`` are refused —
    the URL-keyed-nodes cutover is a hard break (see models.GRAPH_SCHEMA_VERSION).
    """
    try:
        path = _snapshot_dir(job_id) / "graph.snapshot.json"
        if not path.exists():
            return None
        payload = json.loads(path.read_text())
        snap_version = payload.get("schema_version", 1)
        if snap_version != GRAPH_SCHEMA_VERSION:
            logger.warning(
                "Snapshot for job %s has schema_version=%s but this build "
                "requires version %s. Ignoring; re-crawl from seeds.",
                job_id, snap_version, GRAPH_SCHEMA_VERSION,
            )
            return None
        return payload
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
        from ..crawler import GitHubCrawler
        from ..platforms.github import GitHubClient, resolve_cache_dir
        from ..token_env import resolve_github_tokens, tokens_not_set_message

        tokens = resolve_github_tokens()
        if not tokens:
            record.status = JobStatus.FAILED
            record.detail = tokens_not_set_message()
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

        client = GitHubClient(
            tokens=tokens, cache_dir=resolve_cache_dir(default=_api_cache_dir())
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
        from ..crawler import GitHubCrawler
        from ..platforms.github import GitHubGraphQLClient, resolve_cache_dir
        from ..token_env import resolve_github_tokens, tokens_not_set_message

        tokens = resolve_github_tokens()
        if not tokens:
            record.status = JobStatus.FAILED
            record.detail = tokens_not_set_message()
            record.completed_at = datetime.now(timezone.utc)
            return

        client = GitHubGraphQLClient(
            tokens=tokens,
            cache_dir=resolve_cache_dir(default=_api_cache_dir()),
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
