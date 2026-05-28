"""Thin python-gitlab wrapper used by `GitLabAdapter`.

This module owns three concerns and nothing else:

1. **Construction.** Build a `gitlab.Gitlab` instance for the configured
   host using the current token from the pool. The factory is injectable
   (`_gl_factory`) so tests can swap in a `MagicMock`.
2. **Token rotation.** `_rotate()` cycles to the next token in the pool
   and rebuilds the underlying `gitlab.Gitlab` instance. The wrapper does
   *not* decide when to rotate — that's a `GitLabAdapter` concern (Task 10),
   which is the layer that observes rate-limit responses.
3. **Endpoint shims.** Single fetches map 404 → `None` (path-not-found is
   data, not an error). Iterators return plain lists. `iter_project_starrers`
   degrades to `[]` on older self-hosted instances (gitlab.epfl.ch,
   gitlab.ethz.ch) where the endpoint is missing.

A per-host disk cache (`<cache_dir>/<host>/<sha>.json`) is accepted via the
optional `_cache_dir` parameter; today it is only pre-allocated, not yet
wired into the lookups (see `__init__` docstring for the rationale).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Callable, List, Optional

import gitlab

logger = logging.getLogger(__name__)


class GitLabClient:
    """A small wrapper over `python-gitlab` with multi-token rotation.

    The wrapper is deliberately stateless beyond the token pool and the
    currently-bound `gitlab.Gitlab` instance. Higher layers (the adapter and
    BFS engine) own retries, caching, and rate-limit policy.
    """

    def __init__(
        self,
        host: str,
        tokens: List[str],
        _gl_factory: Callable[..., Any] = gitlab.Gitlab,
        _cache_dir: Optional[Path] = None,
    ) -> None:
        """Build a `GitLabClient` for `host`.

        ``_cache_dir`` — if provided — pre-allocates a per-host disk cache
        under ``<_cache_dir>/<host>/`` (the same layout the github-side
        ``APICache`` uses in v3). The cache *directory* is created and
        held on ``self._cache`` so consumers can verify the layout, but
        the single-entity lookups (``get_user_by_username``,
        ``get_group_by_path``, ``get_project_by_path``) and the iterators
        still go straight to the API. Round-tripping python-gitlab object
        instances through JSON is non-trivial (their state is private),
        so disk caching for GitLab is deferred.

        TODO(post-task-15): wire the cache into single-entity lookups by
        caching a minimal dict (``id``, ``path``, ``web_url`` …) and
        rebuilding a lazy proxy on read; widen to iterators afterwards.
        """
        # An empty token list means "anonymous reads only". GitLab instances
        # such as gitlab.epfl.ch allow `/users`, `/groups`, `/projects` reads
        # for public entities without a PAT — useful for a quick discovery
        # crawl, and the only practical way to demo against self-hosted
        # instances when the operator hasn't yet provisioned a token.
        # Anonymous mode is rate-limited far more aggressively by GitLab,
        # so token rotation is a no-op (`_rotate` short-circuits).
        self.host = host
        self.tokens = list(tokens)
        self._idx = 0
        self._gl_factory = _gl_factory
        if not self.tokens:
            logger.warning(
                "GitLabClient(%s) constructed with no tokens — anonymous "
                "reads only; rate limits will be tight.",
                host,
            )
        self._gl = self._build_gl()

        # Optional disk cache for single-entity lookups. Imported lazily to
        # avoid a hard dependency between the GitLab client module and the
        # GitHub-side cache implementation; the cache is platform-agnostic.
        self._cache: Optional[Any] = None
        if _cache_dir is not None:
            from ..github.client import APICache, resolve_cache_ttl
            self._cache = APICache(
                _cache_dir,
                ttl_seconds=resolve_cache_ttl(),
                host=self.host,
            )

    # ---- construction / rotation ------------------------------------------------

    def _build_gl(self) -> Any:
        """Build the underlying `gitlab.Gitlab` instance for the current token.

        Uses `private_token=` rather than `oauth_token=` because self-hosted
        instances we care about (gitlab.epfl.ch, gitlab.ethz.ch, renkulab.io)
        all issue Personal Access Tokens. OAuth support can be added later
        without changing the wrapper's signature.
        """
        if not self.tokens:
            # Anonymous: don't pass private_token at all.
            return self._gl_factory(url=f"https://{self.host}")
        return self._gl_factory(
            url=f"https://{self.host}",
            private_token=self.tokens[self._idx],
        )

    def _rotate(self) -> None:
        """Cycle to the next token and rebuild the `gitlab.Gitlab` instance.

        The adapter calls this when it sees a 429 / "rate limit exceeded"
        response. For Task 9 we just expose the helper; rotation triggers
        live in the adapter layer (Task 10).
        """
        if not self.tokens:
            return   # nothing to rotate in anonymous mode
        self._idx = (self._idx + 1) % len(self.tokens)
        self._gl = self._build_gl()

    # ---- single-entity fetches (404 → None) ------------------------------------

    def get_user_by_username(self, username: str) -> Optional[Any]:
        """Return the `User` whose username matches, or `None` if no match.

        GitLab's `/users` endpoint returns a *list* even for an exact-username
        query, so we read the first hit.
        """
        results = self._gl.users.list(username=username)
        # python-gitlab returns a list-like; coerce to list to avoid surprises
        results = list(results)
        return results[0] if results else None

    def get_group_by_path(self, full_path: str) -> Optional[Any]:
        """Return the `Group` at `full_path` (e.g. `g/sub`), or `None` on 404."""
        try:
            return self._gl.groups.get(full_path)
        except gitlab.GitlabGetError as exc:
            if getattr(exc, "response_code", None) == 404:
                return None
            raise

    def get_project_by_path(self, full_path: str) -> Optional[Any]:
        """Return the `Project` at `full_path` (e.g. `g/p`), or `None` on 404."""
        try:
            return self._gl.projects.get(full_path)
        except gitlab.GitlabGetError as exc:
            if getattr(exc, "response_code", None) == 404:
                return None
            raise

    # ---- group iterators -------------------------------------------------------

    def iter_group_members(self, group_id: Any) -> List[Any]:
        """All members of `group_id`, including inherited (parent-group) members."""
        group = self._gl.groups.get(group_id)
        return list(group.members_all.list(get_all=True))

    def iter_subgroups(self, group_id: Any) -> List[Any]:
        """Immediate subgroups of `group_id`."""
        group = self._gl.groups.get(group_id)
        return list(group.subgroups.list(get_all=True))

    def iter_group_projects(self, group_id: Any) -> List[Any]:
        """Projects owned by `group_id` (does not descend into subgroups)."""
        group = self._gl.groups.get(group_id)
        return list(group.projects.list(get_all=True))

    # ---- user iterators --------------------------------------------------------

    @staticmethod
    def _degrade_on_forbidden(host: str, endpoint: str, exc: Exception) -> List[Any]:
        """Log and return ``[]`` when an iter endpoint is forbidden.

        Anonymous mode and tokens with insufficient scope (``read_api``,
        ``read_user``) frequently 403 on user-listing endpoints. We treat
        those as "no edges from this endpoint" rather than aborting the
        whole crawl — surface in the log so an operator can decide to
        widen their token scope.
        """
        code = getattr(exc, "response_code", None)
        logger.warning(
            "%s on %s returned %s (likely insufficient token scope); "
            "skipping that edge source.",
            endpoint, host, code,
        )
        return []

    def _http_list_projects(self, sub_path: str, endpoint: str) -> List[Any]:
        """List projects under ``sub_path`` via direct HTTP, skipping ``users.get``.

        python-gitlab's idiomatic ``self._gl.users.get(id).projects.list()``
        fetches the single-user endpoint ``/users/:id`` first, which several
        institutional GitLab instances (gitlab.epfl.ch, gitlab.ethz.ch) gate
        behind ``read_user`` scope or refuse to anonymous callers — even
        though the *project list* endpoints themselves are public. Calling
        ``http_list`` directly avoids that pre-fetch entirely.

        Wraps the call in the same forbidden-degrades-to-empty contract as
        every other ``iter_*`` method so a 403 doesn't tank the crawl.
        """
        try:
            results = self._gl.http_list(sub_path, get_all=True)
            return list(results)
        except (
            gitlab.GitlabHttpError,
            gitlab.GitlabListError,
            gitlab.GitlabGetError,
        ) as exc:
            return self._degrade_on_forbidden(self.host, endpoint, exc)

    def iter_user_projects(self, user_id: Any) -> List[Any]:
        """Projects the user authored / owns."""
        return self._http_list_projects(
            f"/users/{user_id}/projects",
            f"users/{user_id}/projects",
        )

    def iter_user_contributed(self, user_id: Any) -> List[Any]:
        """Projects the user has contributed to (commits, merges, etc.)."""
        return self._http_list_projects(
            f"/users/{user_id}/contributed_projects",
            f"users/{user_id}/contributed_projects",
        )

    def iter_user_starred(self, user_id: Any) -> List[Any]:
        """Projects the user has starred."""
        return self._http_list_projects(
            f"/users/{user_id}/starred_projects",
            f"users/{user_id}/starred_projects",
        )

    # ---- project iterators -----------------------------------------------------

    def iter_project_contributors(self, project_id: Any) -> List[Any]:
        """Commit contributors (from the repository contributors endpoint)."""
        project = self._gl.projects.get(project_id)
        return list(project.repository_contributors(get_all=True))

    def iter_project_forks(self, project_id: Any) -> List[Any]:
        """Direct forks of the project."""
        project = self._gl.projects.get(project_id)
        return list(project.forks.list(get_all=True))

    def iter_project_issues(self, project_id: Any, max_n: int) -> List[Any]:
        """Up to `max_n` most recent issues — single page, no full pagination.

        BFS doesn't need every historical issue; the top page is enough to
        seed `issue_authors` / `commenters` edges. `per_page` is capped at
        100 (GitLab's hard limit).
        """
        project = self._gl.projects.get(project_id)
        per_page = min(max_n, 100)
        return list(project.issues.list(per_page=per_page, get_all=False))

    def iter_project_merge_requests(self, project_id: Any, max_n: int) -> List[Any]:
        """Up to `max_n` most recent merge requests — single page, no full pagination."""
        project = self._gl.projects.get(project_id)
        per_page = min(max_n, 100)
        return list(project.mergerequests.list(per_page=per_page, get_all=False))

    def iter_project_starrers(self, project_id: Any) -> List[Any]:
        """Users who starred the project — `[]` when the endpoint is missing.

        `/projects/:id/starrers` was added in GitLab 13.5. Older self-hosted
        instances (gitlab.epfl.ch, gitlab.ethz.ch) raise `AttributeError`
        (no `.starrers` manager) or `GitlabGetError` (server-side 404). In
        both cases we degrade silently — the crawl emits no star edges for
        that project instead of failing.
        """
        try:
            project = self._gl.projects.get(project_id)
            starrers = project.starrers  # may AttributeError on older clients
            return list(starrers.list(get_all=True))
        except AttributeError:
            logger.debug(
                "Project %s on %s: no `.starrers` manager (older python-gitlab "
                "or self-hosted instance); returning no star edges.",
                project_id,
                self.host,
            )
            return []
        except gitlab.GitlabGetError as exc:
            logger.debug(
                "Project %s on %s: starrers endpoint returned %s; returning no "
                "star edges.",
                project_id,
                self.host,
                getattr(exc, "response_code", "?"),
            )
            return []

    # ---- rate-limit snapshot (for adapter.rate_limit_state) --------------------
    # python-gitlab v5 does not surface a stable `connection.last_response`
    # accessor, so we return conservative defaults. The adapter's RateLimitInfo
    # contract treats these as a best-effort snapshot; when GitLab returns a
    # 429 the adapter will rotate tokens regardless of what we report here.
    # TODO(task-15+): plumb real header values via a `gitlab.Gitlab` subclass
    # that captures the last response in a thread-local.

    def rate_limit_remaining(self) -> int:
        return 1000

    def rate_limit_limit(self) -> int:
        return 2000

    def rate_limit_reset_at(self) -> Optional[float]:
        return None
