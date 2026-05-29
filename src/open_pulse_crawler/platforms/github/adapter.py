"""``GitHubAdapter`` — the concrete ``PlatformAdapter`` for github.com.

The adapter is the thin seam between the BFS engine and ``GitHubClient``.
It does four things and nothing else:

* ``classify``   — shape-match a URL to a ``NodeKind`` (or ``None``).
* ``normalize_uri`` — delegate to :func:`open_pulse_crawler.node_id.canonical_url`.
* ``fetch``      — call the right ``client.get_*`` method and pass the
                   result through unchanged. Building the Pydantic model
                   from the API response is the client's job, not the
                   adapter's.
* ``expand``     — convert the model's platform-shorthand lists (logins,
                   ``owner/repo`` strings) to canonical URLs and emit
                   explicit :class:`~open_pulse_crawler.platforms.base.Edge`
                   tuples, honouring the per-crawl :class:`ExpandOpts`.

A single class supports both ``github.com`` and a future hypothetical
enterprise host (``github.example.org``); the constructor takes the host,
and ``expand`` builds destination URLs against ``self.instance_host``.

This class deliberately does **not** add new methods to ``GitHubClient``
beyond the existing ``get_user`` / ``get_organization`` / ``get_repository``
(and the small ``rate_limit_remaining`` / ``rate_limit_limit`` accessors
needed by ``rate_limit_state``).
"""
from __future__ import annotations

from typing import ClassVar, Iterable, Optional
from urllib.parse import urlparse

from ...models import OrgModel, RepoModel, TeamModel, UserModel
from ...node_id import NodeKind, canonical_url, is_team_url
from ..base import Edge, ExpandOpts, PlatformAdapter, RateLimitInfo


# One-segment paths under ``github.com`` that are reserved by GitHub itself
# (routes for org/team pages, marketplace, etc.) and therefore can never be a
# user-or-org login. classify() must return ``None`` for these so the crawler
# skips them instead of trying to fetch a nonexistent user.
_RESERVED_TOP_PATHS = frozenset(
    {
        "orgs",
        "settings",
        "marketplace",
        "explore",
        "topics",
        "trending",
        "collections",
        "events",
        "notifications",
        "issues",
        "pulls",
        "search",
        "login",
        "join",
        "new",
        "about",
        "pricing",
        "features",
        "security",
        "enterprise",
        "customer-stories",
        "readme",
        "sponsors",
    }
)


class GitHubAdapter(PlatformAdapter):
    """``PlatformAdapter`` over the existing ``GitHubClient``.

    One instance per configured host. For github.com the model field
    ``platform`` defaults to ``"github"``; ``instance_host`` is the full
    hostname used as the registry key and as the URL-building base in
    ``expand``.
    """

    platform: ClassVar[str] = "github"

    def __init__(self, client, instance_host: str = "github.com") -> None:
        # ``client`` is a ``GitHubClient`` in production; tests inject a
        # MagicMock. We keep it on the ``_client`` attribute so tests can
        # reach it directly (e.g. ``adapter._client.get_user``).
        self._client = client
        self.instance_host = instance_host

    # ------------------------------------------------------------------ helpers

    def _user_url(self, login: str) -> str:
        """Build a canonical user/org URL on this adapter's host."""
        return canonical_url(self.instance_host, login)

    def _repo_url(self, full_name: str) -> str:
        """Build a canonical repo URL on this adapter's host from ``owner/repo``."""
        return canonical_url(self.instance_host, full_name)

    # ----------------------------------------------------------------- classify

    def classify(self, uri: str) -> Optional[NodeKind]:
        """Shape-match a URL to a ``NodeKind``.

        Empty paths, reserved top-level paths (``/orgs``, ``/settings``,
        etc.), and paths with an unrecognised depth return ``None`` so the
        crawler can skip them instead of attempting a doomed fetch.
        """
        parsed = urlparse(uri)
        path = (parsed.path or "").strip("/")
        if not path:
            return None
        # Teams live under ``/orgs/<org>/teams/<slug>``; check first so the
        # one-segment ``orgs`` reserved-path check below doesn't swallow them.
        if is_team_url(uri):
            return NodeKind.TEAM
        parts = path.split("/")
        if len(parts) == 1:
            if parts[0].lower() in _RESERVED_TOP_PATHS:
                return None
            return NodeKind.USER_OR_ORG
        if len(parts) == 2:
            # Reserved owner-level paths (e.g. ``/orgs/anthropic``) aren't
            # repos either.
            if parts[0].lower() in _RESERVED_TOP_PATHS:
                return None
            return NodeKind.REPO
        return None

    # ------------------------------------------------------------ normalize_uri

    def normalize_uri(self, raw: str) -> str:
        """Return the canonical form of ``raw``.

        Delegates to :func:`open_pulse_crawler.node_id.canonical_url` — that
        helper already lowercases the scheme + host, strips trailing slashes,
        and drops fragments / queries. We just parse ``raw`` and hand off.
        """
        parsed = urlparse(raw)
        host = (parsed.hostname or self.instance_host).lower()
        return canonical_url(host, parsed.path or "/")

    # -------------------------------------------------------------------- fetch

    def fetch(self, uri: str):
        """Route ``uri`` to the right ``client.get_*`` method.

        Returns whatever the client returned, unchanged — building the
        Pydantic model from the API response is the client's job (or, in
        the current crawler, the per-process helpers in ``crawler.py``).
        The adapter is intentionally pass-through here so tests can mock
        the client return value directly.
        """
        kind = self.classify(uri)
        if kind == NodeKind.REPO:
            return self._client.get_repository(uri)
        if kind == NodeKind.TEAM:
            # ``GitHubClient`` doesn't expose a single-team fetcher today
            # (teams are populated as a side effect of org fetches). The
            # adapter routes to ``get_team`` so the contract is complete;
            # production wiring lands when Task 6 plugs the adapter into
            # the BFS engine. Until then, ``get_team`` may be missing, so
            # we guard with ``getattr`` and return ``None`` (matching the
            # other branches' "we don't know how" semantics) instead of
            # raising ``AttributeError``.
            get_team = getattr(self._client, "get_team", None)
            return get_team(uri) if get_team is not None else None
        if kind == NodeKind.USER_OR_ORG:
            # USER_OR_ORG is genuinely ambiguous at the URL level; try user
            # first, fall back to organization — same order the crawler uses
            # today in ``_process_node`` for the same reason.
            node = self._client.get_user(uri)
            if node is not None:
                return node
            return self._client.get_organization(uri)
        return None

    # ------------------------------------------------------------------- expand

    def expand(self, node, opts: ExpandOpts) -> Iterable[Edge]:
        """Emit outgoing edges from ``node``, honouring ``opts``.

        This is the only place that converts model-stored shorthand
        (logins / ``owner/repo``) to canonical URLs — everywhere else
        keeps the compact internal form. Edges are emitted as a flat
        iterable; the BFS engine consumes them and queues their endpoints.
        """
        if isinstance(node, UserModel):
            yield from self._expand_user(node, opts)
        elif isinstance(node, OrgModel):
            yield from self._expand_org(node, opts)
        elif isinstance(node, RepoModel):
            yield from self._expand_repo(node, opts)
        elif isinstance(node, TeamModel):
            yield from self._expand_team(node, opts)
        # Unknown node types yield nothing — defensive default rather than
        # raising, so a future model subclass doesn't crash the BFS.

    def _expand_user(self, user: UserModel, opts: ExpandOpts) -> Iterable[Edge]:
        # Followers: edge points *into* the user (follower → followee).
        for follower in user.followers:
            yield Edge(
                src=self._user_url(follower),
                kind="follower_of",
                dst=user.url,
            )
        # Following: same edge kind, opposite direction (user → followee).
        for followee in user.following:
            yield Edge(
                src=user.url,
                kind="follower_of",
                dst=self._user_url(followee),
            )
        for repo_full in user.starred_repositories:
            yield Edge(
                src=user.url,
                kind="starred",
                dst=self._repo_url(repo_full),
            )
        for repo_full in user.watched_repositories:
            yield Edge(
                src=user.url,
                kind="watched",
                dst=self._repo_url(repo_full),
            )
        for repo_full in user.authored_repositories:
            yield Edge(
                src=user.url,
                kind="authored",
                dst=self._repo_url(repo_full),
            )
        for repo_full in user.forked_repositories:
            yield Edge(
                src=user.url,
                kind="forked",
                dst=self._repo_url(repo_full),
            )

    def _expand_org(self, org: OrgModel, opts: ExpandOpts) -> Iterable[Edge]:
        for member_login in org.members:
            yield Edge(
                src=self._user_url(member_login),
                kind="member_of",
                dst=org.url,
            )
        for repo_full in org.authored_repositories:
            yield Edge(
                src=org.url,
                kind="authored",
                dst=self._repo_url(repo_full),
            )
        for repo_full in org.forked_repositories:
            yield Edge(
                src=org.url,
                kind="forked",
                dst=self._repo_url(repo_full),
            )

    def _expand_repo(self, repo: RepoModel, opts: ExpandOpts) -> Iterable[Edge]:
        # Contributors: opts.max_contributors caps the count (a `None` slice
        # bound keeps the whole list, matching the crawler's existing
        # ``_contributor_limit`` semantics).
        limit = opts.max_contributors
        contributors = repo.contributors[:limit] if limit is not None else repo.contributors
        for contributor in contributors:
            yield Edge(
                src=self._user_url(contributor),
                kind="contributor_of",
                dst=repo.url,
            )

        if repo.forked_from:
            yield Edge(
                src=repo.url,
                kind="forked_from",
                dst=self._repo_url(repo.forked_from),
            )

        if opts.crawl_issues:
            for author in repo.issue_authors:
                yield Edge(
                    src=self._user_url(author),
                    kind="opened_issue_in",
                    dst=repo.url,
                )

        if opts.crawl_prs:
            for author in repo.pr_authors:
                yield Edge(
                    src=self._user_url(author),
                    kind="opened_pr_in",
                    dst=repo.url,
                )
            for reviewer in repo.pr_reviewers:
                yield Edge(
                    src=self._user_url(reviewer),
                    kind="reviewed_pr_in",
                    dst=repo.url,
                )
            for commenter in repo.commenters:
                yield Edge(
                    src=self._user_url(commenter),
                    kind="commented_in",
                    dst=repo.url,
                )

        # Dependencies: this repo `depends_on` each entry.
        if opts.crawl_dependencies:
            for dep_full in repo.dependencies:
                yield Edge(
                    src=repo.url,
                    kind="depends_on",
                    dst=self._repo_url(dep_full),
                )
        # Dependents: each entry `depends_on` this repo (reversed direction).
        if opts.crawl_dependents:
            for dep_full in repo.dependents:
                yield Edge(
                    src=self._repo_url(dep_full),
                    kind="depends_on",
                    dst=repo.url,
                )

    def _expand_team(self, team: TeamModel, opts: ExpandOpts) -> Iterable[Edge]:
        for member_login in team.members:
            yield Edge(
                src=self._user_url(member_login),
                kind="member_of",
                dst=team.url,
            )
        for repo_full in team.repositories:
            yield Edge(
                src=self._repo_url(repo_full),
                kind="repo_of",
                dst=team.url,
            )

    # ----------------------------------------------------------- rate_limit

    def rate_limit_state(self) -> RateLimitInfo:
        """Snapshot of the underlying client's rate-limit budget.

        Reads the client's flat ``rate_limit_remaining`` /
        ``rate_limit_limit`` accessors. ``GitHubClient`` exposes these as
        ``@property`` over its multi-token state; mocks set them directly.
        """
        remaining = int(getattr(self._client, "rate_limit_remaining", 0) or 0)
        limit = int(getattr(self._client, "rate_limit_limit", 0) or 0)
        reset_at = getattr(self._client, "rate_limit_reset_at", None)
        return RateLimitInfo(remaining=remaining, limit=limit, reset_at=reset_at)
