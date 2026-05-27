"""PlatformAdapter ABC and supporting DTOs.

The BFS crawler delegates all platform-specific work to a `PlatformAdapter`
implementation looked up from a `PlatformRegistry` by URL host. Adapters
return concrete Node subclasses (UserModel / GitLabProjectModel / etc.)
from `fetch`, and emit explicit `Edge` tuples from `expand`.
"""
from __future__ import annotations
from abc import ABC, abstractmethod
from typing import ClassVar, Iterable, Optional

from pydantic import BaseModel


class ExpandOpts(BaseModel):
    """Per-crawl knobs that govern what edges `PlatformAdapter.expand` emits.

    Adapters silently ignore options that don't apply to their platform —
    e.g. the GitLab adapter is a no-op for `crawl_dependents`.
    """
    crawl_issues: bool = False
    crawl_prs: bool = False
    crawl_dependencies: bool = False
    crawl_dependents: bool = False
    crawl_stars: bool = False
    min_stars: int = 0
    max_contributors: Optional[int] = None
    issue_max: int = 100
    pr_max: int = 100


class Edge(BaseModel):
    """A directed edge in the crawled graph.

    `src` and `dst` are canonical node URIs (the same form used as keys in
    `GraphData.users`/`orgs`/`repos`/`teams`). `kind` is a free-form string
    naming the relation — e.g. `contributor_of`, `forked_from`, `member_of`.
    """
    src: str
    kind: str
    dst: str


class RateLimitInfo(BaseModel):
    """Snapshot of a platform's rate-limit state for progress reporting."""
    remaining: int
    limit: int
    reset_at: Optional[float] = None


class PlatformAdapter(ABC):
    """Single seam between the BFS engine and a source platform.

    Concrete subclasses bind to one instance host (e.g. `github.com`,
    `gitlab.epfl.ch`). One adapter instance per configured host.
    """

    platform: ClassVar[str]   # short id, e.g. "github" or "gitlab"
    instance_host: str        # full hostname, set by __init__

    @abstractmethod
    def classify(self, uri: str):
        """Return the abstract NodeKind (or platform-specific kind enum)
        for `uri`, or `None` if the URI doesn't resolve to a known kind."""

    @abstractmethod
    def fetch(self, uri: str):
        """Fetch the platform entity at `uri` and return a concrete Node
        subclass populated from the source-of-truth API."""

    @abstractmethod
    def expand(self, node, opts: ExpandOpts) -> Iterable[Edge]:
        """Emit outgoing edges from `node`, honoring the per-crawl `opts`."""

    @abstractmethod
    def normalize_uri(self, raw: str) -> str:
        """Return the canonical form of `raw` for this platform."""

    @abstractmethod
    def rate_limit_state(self) -> RateLimitInfo:
        """Current rate-limit snapshot for the underlying HTTP client(s)."""
