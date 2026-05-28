"""``GitLabAdapter`` — the concrete ``PlatformAdapter`` for GitLab instances.

Multi-instance by construction: one ``GitLabAdapter`` per GitLab host
(``gitlab.com``, ``gitlab.epfl.ch``, ``gitlab.ethz.ch``, ``renkulab.io``,
...), each wrapping its own :class:`GitLabClient`. The
``PlatformRegistry`` (Task 4) keys by host, so the BFS engine dispatches
the right adapter for each URL.

Path disambiguation
-------------------

GitLab URLs are genuinely ambiguous at the URL level — only an API probe
can tell user from group, project from subgroup:

* ``https://<host>/foo``      — user *or* top-level group.
* ``https://<host>/a/b``      — project ``a/b`` *or* subgroup ``a/b``.
* ``https://<host>/a/b/c/..`` — deeper project *or* deeper subgroup.

Resolution strategy:

* **Top-level path** (one segment): probe ``users?username=`` first; if a
  user matches, classify as user. Otherwise try ``groups/<path>``; if it
  exists, classify as group. Otherwise ``None``.
* **Nested path** (two+ segments): probe ``projects/<full_path>`` first
  (the more likely case for nested paths); if it exists, classify as
  project. Otherwise try ``groups/<full_path>``; if it exists, classify
  as group/subgroup. Otherwise ``None``.

Results are cached on ``self._kind_cache`` keyed by the URL path string,
so ``classify`` and ``fetch`` for the same URL only probe once.

What this module ships (Tasks 10 + 11)
--------------------------------------

``classify``, ``fetch``, ``normalize_uri``, ``rate_limit_state`` (Task 10)
plus ``expand`` (Task 11). ``expand`` walks the GitLab client's iterator
endpoints and yields :class:`Edge` instances pointing at canonical URLs
on this adapter's instance host.
"""

from __future__ import annotations

from typing import Any, ClassVar, Iterable, Optional
from urllib.parse import urlparse

from ...models import (
    GitLabGroupModel,
    GitLabProjectModel,
    GitLabUserModel,
)
from ...node_id import NodeKind, canonical_url
from ..base import Edge, ExpandOpts, PlatformAdapter, RateLimitInfo


class GitLabAdapter(PlatformAdapter):
    """``PlatformAdapter`` over a single :class:`GitLabClient`.

    One instance per configured host. ``instance_host`` is the full
    hostname used as the registry key and as the URL-building base for
    derived URLs (parent groups, project namespaces, fork upstreams).
    """

    platform: ClassVar[str] = "gitlab"

    def __init__(self, client, instance_host: str) -> None:
        # ``client`` is a ``GitLabClient`` in production; tests inject a
        # ``MagicMock``. The kind cache is keyed by URL path (e.g. ``"alice"``
        # or ``"grp/sub"``) and holds one of ``"user" | "group" | "project"
        # | "unknown"``. Both ``classify`` and ``fetch`` consult it.
        self._client = client
        self.instance_host = instance_host
        self._kind_cache: dict[str, str] = {}

    # ------------------------------------------------------------------ helpers

    def _path_of(self, uri: str) -> str:
        """Return the URL's path (without leading or trailing slash)."""
        return (urlparse(uri).path or "").strip("/")

    def _url_for_path(self, path: str) -> str:
        """Build a canonical URL on this adapter's host from a bare path."""
        return canonical_url(self.instance_host, path)

    @staticmethod
    def _safe_str(value: Any, default: str = "") -> str:
        """Coerce ``value`` to a string only when it really is one.

        python-gitlab returns plain strings for ``name`` / ``visibility``
        / etc., but tests inject ``MagicMock`` instances whose missing
        attributes return ``MagicMock`` themselves. Without this guard
        pydantic rejects those with ``string_type`` errors. We treat
        anything non-``str`` as missing and fall back to ``default``.
        """
        return value if isinstance(value, str) else default

    @staticmethod
    def _safe_int(value: Any, default: int = 0) -> int:
        """Coerce ``value`` to an int when possible, else fall back."""
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    def _resolve_kind(self, path: str) -> str:
        """Probe the GitLab API to determine what ``path`` is.

        Returns one of ``"user"``, ``"group"``, ``"project"``, or
        ``"unknown"``. The result is cached on ``self._kind_cache`` so a
        subsequent ``classify`` or ``fetch`` call for the same path uses
        the cached value instead of re-probing.
        """
        cached = self._kind_cache.get(path)
        if cached is not None:
            return cached

        # Empty path: nothing to probe.
        if not path:
            self._kind_cache[path] = "unknown"
            return "unknown"

        segments = path.split("/")
        if len(segments) == 1:
            # Top-level: user-first, then group.
            user = self._client.get_user_by_username(segments[0])
            if user is not None:
                self._kind_cache[path] = "user"
                return "user"
            group = self._client.get_group_by_path(path)
            if group is not None:
                self._kind_cache[path] = "group"
                return "group"
            self._kind_cache[path] = "unknown"
            return "unknown"

        # Nested: project-first (the more likely case), then subgroup.
        project = self._client.get_project_by_path(path)
        if project is not None:
            self._kind_cache[path] = "project"
            return "project"
        group = self._client.get_group_by_path(path)
        if group is not None:
            self._kind_cache[path] = "group"
            return "group"
        self._kind_cache[path] = "unknown"
        return "unknown"

    # ----------------------------------------------------------------- classify

    def classify(self, uri: str) -> Optional[NodeKind]:
        """Map ``uri`` onto the cross-platform :class:`NodeKind` enum.

        GitLab's user/group distinction collapses to ``USER_OR_ORG`` here
        to stay compatible with the existing enum. The internal
        user-vs-group split is preserved in the kind cache for ``fetch``.

        The URI is canonicalized first (mirrors :meth:`fetch`).
        """
        uri = self.normalize_uri(uri)
        path = self._path_of(uri)
        kind = self._resolve_kind(path)
        if kind == "user" or kind == "group":
            return NodeKind.USER_OR_ORG
        if kind == "project":
            return NodeKind.REPO
        return None

    # ------------------------------------------------------------ normalize_uri

    def normalize_uri(self, raw: str) -> str:
        """Return the canonical form of ``raw``.

        Delegates to :func:`open_pulse_crawler.node_id.canonical_url`,
        which lowercases the host, enforces ``https``, and strips
        trailing slashes.

        Also rewrites GitLab's dashboard-form user URLs to their
        canonical profile form: ``/users/<name>`` → ``/<name>``. GitLab
        emits the ``/users/`` prefix on some redirects and in API
        responses; the canonical profile page (and the form we key the
        graph on) is the bare ``/<name>``.
        """
        parsed = urlparse(raw)
        host = (parsed.hostname or self.instance_host).lower()
        path = parsed.path or "/"
        # Strip the GitLab dashboard `/users/` prefix for user profile URLs.
        # Only top-level `/users/<name>` is rewritten; we don't touch
        # nested `/users/<name>/something` since that's not a user profile.
        if path.startswith("/users/"):
            tail = path[len("/users/"):]
            if tail and "/" not in tail.rstrip("/"):
                path = "/" + tail.rstrip("/")
        return canonical_url(host, path)

    # -------------------------------------------------------------------- fetch

    def fetch(self, uri: str):
        """Return the populated GitLab model for ``uri``.

        Routes to one of ``GitLabUserModel`` / ``GitLabGroupModel`` /
        ``GitLabProjectModel`` based on the cached/probed kind. Returns
        ``None`` when the URL doesn't resolve to a known entity.

        The URI is canonicalized first (lowercased host, ``/users/<name>``
        → ``/<name>``, trailing slash stripped) so callers can pass the
        URL form they have on hand — the dashboard form, the bare
        profile URL, etc. — without having to pre-normalize.
        """
        uri = self.normalize_uri(uri)
        path = self._path_of(uri)
        kind = self._resolve_kind(path)
        if kind == "user":
            return self._build_user(uri, path)
        if kind == "group":
            return self._build_group(uri, path)
        if kind == "project":
            return self._build_project(uri, path)
        return None

    def _build_user(self, uri: str, path: str) -> Optional[GitLabUserModel]:
        raw = self._client.get_user_by_username(path)
        if raw is None:
            return None
        return GitLabUserModel(
            url=uri,
            login=raw.username,
            name=self._safe_str(getattr(raw, "name", "")),
            id=self._safe_int(getattr(raw, "id", 0)),
            platform="gitlab",
            state=self._safe_str(getattr(raw, "state", "active"), "active") or "active",
            public_email=self._safe_str(getattr(raw, "public_email", "")),
        )

    def _build_group(self, uri: str, path: str) -> Optional[GitLabGroupModel]:
        raw = self._client.get_group_by_path(path)
        if raw is None:
            return None
        # Subgroup parents: ``full_path`` is slash-separated; strip the last
        # segment to build the parent group URL. ``parent_id`` being None
        # signals a top-level group.
        parent_url: Optional[str] = None
        parent_id = getattr(raw, "parent_id", None)
        full_path = getattr(raw, "full_path", path) or path
        if parent_id is not None and "/" in full_path:
            parent_path = full_path.rsplit("/", 1)[0]
            parent_url = self._url_for_path(parent_path)
        return GitLabGroupModel(
            url=uri,
            login=full_path,
            name=self._safe_str(getattr(raw, "name", "")),
            id=self._safe_int(getattr(raw, "id", 0)),
            platform="gitlab",
            parent=parent_url,
            visibility=self._safe_str(getattr(raw, "visibility", "public"), "public") or "public",
        )

    def _build_project(self, uri: str, path: str) -> Optional[GitLabProjectModel]:
        raw = self._client.get_project_by_path(path)
        if raw is None:
            return None
        full_name = getattr(raw, "path_with_namespace", path) or path

        # Fork detection: GitLab attaches the upstream project as a dict on
        # ``forked_from_project`` (or None for original projects).
        forked_from_raw = getattr(raw, "forked_from_project", None)
        is_fork = bool(forked_from_raw)
        forked_from_url: Optional[str] = None
        if is_fork:
            # python-gitlab returns this as a dict; tests confirm shape.
            upstream_path = None
            if isinstance(forked_from_raw, dict):
                upstream_path = forked_from_raw.get("path_with_namespace")
            else:
                upstream_path = getattr(forked_from_raw, "path_with_namespace", None)
            if upstream_path:
                forked_from_url = self._url_for_path(upstream_path)

        # Namespace: the owning user or group, returned by GitLab as a dict
        # with ``full_path`` set. We keep the canonical URL form so callers
        # can join cross-namespace.
        namespace_url: Optional[str] = None
        ns = getattr(raw, "namespace", None)
        if isinstance(ns, dict):
            ns_path = ns.get("full_path")
            if ns_path:
                namespace_url = self._url_for_path(ns_path)
        elif ns is not None:
            ns_path = getattr(ns, "full_path", None)
            if ns_path:
                namespace_url = self._url_for_path(ns_path)

        return GitLabProjectModel(
            url=uri,
            full_name=full_name,
            name=self._safe_str(getattr(raw, "name", "")),
            id=self._safe_int(getattr(raw, "id", 0)),
            platform="gitlab",
            visibility=self._safe_str(getattr(raw, "visibility", "public"), "public") or "public",
            is_fork=is_fork,
            forked_from=forked_from_url,
            namespace=namespace_url,
        )

    # ------------------------------------------------------------------- expand

    def expand(self, node, opts: ExpandOpts) -> Iterable[Edge]:
        """Emit outgoing edges from ``node``, honouring ``opts``.

        Dispatch on the concrete GitLab model subclass. Unknown node types
        yield nothing — a defensive default rather than raising, so a
        future model subclass doesn't crash the BFS engine.
        """
        if isinstance(node, GitLabUserModel):
            yield from self._expand_user(node, opts)
        elif isinstance(node, GitLabGroupModel):
            yield from self._expand_group(node, opts)
        elif isinstance(node, GitLabProjectModel):
            yield from self._expand_project(node, opts)

    # -- per-kind helpers -------------------------------------------------

    def _expand_user(self, node: GitLabUserModel, opts: ExpandOpts) -> Iterable[Edge]:
        # Owned projects → ``authored`` edges (user owns the namespace).
        for proj in self._client.iter_user_projects(node.id):
            path = self._get_field(proj, "path_with_namespace")
            if not path:
                continue
            yield Edge(src=node.url, kind="authored", dst=self._url_for_path(path))

        # Starred projects → ``starred`` edges.
        for proj in self._client.iter_user_starred(node.id):
            path = self._get_field(proj, "path_with_namespace")
            if not path:
                continue
            yield Edge(src=node.url, kind="starred", dst=self._url_for_path(path))

        # Contributed-to projects: we don't keep them on the user model
        # (the contributor list is recorded on the project's side), but the
        # BFS engine still needs to discover them, so emit the edge.
        for proj in self._client.iter_user_contributed(node.id):
            path = self._get_field(proj, "path_with_namespace")
            if not path:
                continue
            yield Edge(src=node.url, kind="contributor_of", dst=self._url_for_path(path))

    def _expand_group(self, node: GitLabGroupModel, opts: ExpandOpts) -> Iterable[Edge]:
        # Members → ``member_of`` edges (user → group).
        for member in self._client.iter_group_members(node.id):
            username = getattr(member, "username", None)
            if not username:
                continue
            yield Edge(
                src=self._url_for_path(username),
                kind="member_of",
                dst=node.url,
            )

        # Subgroups → ``subgroup_of`` edges (child → parent).
        for sub in self._client.iter_subgroups(node.id):
            full_path = getattr(sub, "full_path", None)
            if not full_path:
                continue
            yield Edge(
                src=self._url_for_path(full_path),
                kind="subgroup_of",
                dst=node.url,
            )

        # Group-owned projects → ``authored`` edges (group → project).
        for proj in self._client.iter_group_projects(node.id):
            path = getattr(proj, "path_with_namespace", None)
            if not path:
                continue
            yield Edge(src=node.url, kind="authored", dst=self._url_for_path(path))

    def _expand_project(self, node: GitLabProjectModel, opts: ExpandOpts) -> Iterable[Edge]:
        # Contributors: python-gitlab's ``repository_contributors`` returns
        # commit-author records keyed by email/name — no stable user
        # identifier. We emit a ``contributor_of`` edge only when the entry
        # carries a ``username`` field (some GitLab instances enrich it);
        # otherwise we silently skip since there's no real user to point at.
        # ``opts.max_contributors`` caps the number of *emitted* edges
        # (skipped-no-username entries do not count toward the cap).
        limit = opts.max_contributors
        emitted = 0
        for contributor in self._client.iter_project_contributors(node.id):
            if limit is not None and emitted >= limit:
                break
            username = self._get_username(contributor)
            if not username:
                continue
            yield Edge(
                src=self._url_for_path(username),
                kind="contributor_of",
                dst=node.url,
            )
            emitted += 1

        # Forks: edge points from the downstream fork *to* this upstream.
        for fork in self._client.iter_project_forks(node.id):
            path = getattr(fork, "path_with_namespace", None)
            if not path:
                continue
            yield Edge(
                src=self._url_for_path(path),
                kind="forked_from",
                dst=node.url,
            )

        # Issues — only fetched when crawl_issues is on (the client call
        # itself is gated so we never hit GitLab when the flag is off).
        if opts.crawl_issues:
            for issue in self._client.iter_project_issues(node.id, max_n=opts.issue_max):
                username = self._author_username(getattr(issue, "author", None))
                if not username:
                    continue
                yield Edge(
                    src=self._url_for_path(username),
                    kind="opened_issue_in",
                    dst=node.url,
                )

        # Merge requests — same shape as issues, gated on crawl_prs.
        if opts.crawl_prs:
            for mr in self._client.iter_project_merge_requests(node.id, max_n=opts.pr_max):
                username = self._author_username(getattr(mr, "author", None))
                if not username:
                    continue
                yield Edge(
                    src=self._url_for_path(username),
                    kind="opened_pr_in",
                    dst=node.url,
                )

        # Starrers — gated on crawl_stars. GitLabClient already returns []
        # when the underlying endpoint is missing on older self-hosted
        # instances, so we just iterate.
        if opts.crawl_stars:
            for starrer in self._client.iter_project_starrers(node.id):
                username = self._starrer_username(starrer)
                if not username:
                    continue
                yield Edge(
                    src=self._url_for_path(username),
                    kind="starred",
                    dst=node.url,
                )

    # -- shape helpers ----------------------------------------------------

    @staticmethod
    def _get_username(entry: Any) -> Optional[str]:
        """Best-effort ``username`` lookup on a dict or attr-style object."""
        if isinstance(entry, dict):
            value = entry.get("username")
        else:
            value = getattr(entry, "username", None)
        return value if isinstance(value, str) and value else None

    @staticmethod
    def _get_field(entry: Any, name: str) -> Optional[str]:
        """Read a string field from either a dict (``http_list`` payload) or
        a typed python-gitlab object. The GitLab client's user-listing
        endpoints come back as plain dicts (because we bypass
        ``users.get(...)`` to avoid its 403-on-no-scope failure), while
        group/project iterators still yield typed objects. This helper
        absorbs both forms so the adapter doesn't have to branch."""
        if isinstance(entry, dict):
            value = entry.get(name)
        else:
            value = getattr(entry, name, None)
        return value if isinstance(value, str) and value else None

    @classmethod
    def _author_username(cls, author: Any) -> Optional[str]:
        """Extract the ``username`` from python-gitlab's author dict/object."""
        if author is None:
            return None
        return cls._get_username(author)

    @classmethod
    def _starrer_username(cls, starrer: Any) -> Optional[str]:
        """Pull ``username`` from a starrer entry's nested ``user`` payload."""
        if isinstance(starrer, dict):
            user = starrer.get("user")
        else:
            user = getattr(starrer, "user", None)
        return cls._get_username(user) if user is not None else None

    # ----------------------------------------------------------- rate_limit

    def rate_limit_state(self) -> RateLimitInfo:
        """Snapshot of the underlying client's rate-limit budget.

        :class:`GitLabClient` exposes ``rate_limit_*`` as plain methods
        (not properties) because python-gitlab v5 doesn't surface a stable
        last-response accessor — Task 9 returns conservative defaults
        until a richer wrapper lands.
        """
        remaining = int(self._client.rate_limit_remaining() or 0)
        limit = int(self._client.rate_limit_limit() or 0)
        reset_at = self._client.rate_limit_reset_at()
        return RateLimitInfo(remaining=remaining, limit=limit, reset_at=reset_at)
