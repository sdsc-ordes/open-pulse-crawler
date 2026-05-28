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

What this task ships (Task 10)
------------------------------

``classify``, ``fetch``, ``normalize_uri``, ``rate_limit_state``. The
``expand`` method raises ``NotImplementedError`` — Task 11 implements
edge emission against the GitLab subclass models.
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
        """
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
        """
        parsed = urlparse(raw)
        host = (parsed.hostname or self.instance_host).lower()
        return canonical_url(host, parsed.path or "/")

    # -------------------------------------------------------------------- fetch

    def fetch(self, uri: str):
        """Return the populated GitLab model for ``uri``.

        Routes to one of ``GitLabUserModel`` / ``GitLabGroupModel`` /
        ``GitLabProjectModel`` based on the cached/probed kind. Returns
        ``None`` when the URL doesn't resolve to a known entity.
        """
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
        """Edge emission lands in Task 11. The placeholder raises so the
        class is instantiable but accidental use fails loudly instead of
        silently emitting nothing.
        """
        raise NotImplementedError("Task 11 implements expand()")

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
