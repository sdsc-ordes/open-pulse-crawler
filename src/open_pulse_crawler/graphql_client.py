"""GraphQL-backed GitHub client.

Duck-type compatible with `GitHubClient` for the surface the crawler uses
(`tokens`, `cache`, `current_token_idx`, `semaphore`, `get_user`,
`get_organization`, `get_repository`, `get_stats`). `get_*` methods return
dicts in the same shape produced by `GitHubClient`'s file cache, so the
crawler routes through its cached-path code unchanged.

Three things still go over REST regardless of mode:
- `contributors` (no usable GraphQL endpoint)
- SBOM dependencies (REST `/dependency-graph/sbom`)
- "Used by" dependents (HTML-scraped via `github_dependents_info`)

The first is fetched inline here per repo; the second and third are
fetched by the crawler itself via the existing helpers.
"""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)

GRAPHQL_URL = "https://api.github.com/graphql"
REST_BASE = "https://api.github.com"


_USER_QUERY = """
query User($login: String!) {
  rateLimit { cost remaining resetAt }
  user(login: $login) {
    login
    name
    databaseId
    followers(first: 100) {
      nodes { login }
      pageInfo { hasNextPage endCursor }
    }
    following(first: 100) {
      nodes { login }
      pageInfo { hasNextPage endCursor }
    }
    starredRepositories(first: 100) {
      nodes { nameWithOwner }
      pageInfo { hasNextPage endCursor }
    }
    watching(first: 100) {
      nodes { nameWithOwner }
      pageInfo { hasNextPage endCursor }
    }
    organizations(first: 100) { nodes { login } }
    repositories(
      first: 100,
      ownerAffiliations: OWNER,
      orderBy: {field: UPDATED_AT, direction: DESC}
    ) {
      nodes { nameWithOwner isFork }
      pageInfo { hasNextPage endCursor }
    }
  }
}
"""

_USER_STARRED_PAGE = """
query UserStarred($login: String!, $cursor: String!) {
  rateLimit { cost remaining resetAt }
  user(login: $login) {
    starredRepositories(first: 100, after: $cursor) {
      nodes { nameWithOwner }
      pageInfo { hasNextPage endCursor }
    }
  }
}
"""

_USER_REPOS_PAGE = """
query UserRepos($login: String!, $cursor: String!) {
  rateLimit { cost remaining resetAt }
  user(login: $login) {
    repositories(
      first: 100,
      ownerAffiliations: OWNER,
      orderBy: {field: UPDATED_AT, direction: DESC},
      after: $cursor
    ) {
      nodes { nameWithOwner isFork }
      pageInfo { hasNextPage endCursor }
    }
  }
}
"""

_USER_FOLLOWERS_PAGE = """
query UserFollowers($login: String!, $cursor: String!) {
  rateLimit { cost remaining resetAt }
  user(login: $login) {
    followers(first: 100, after: $cursor) {
      nodes { login }
      pageInfo { hasNextPage endCursor }
    }
  }
}
"""

_USER_FOLLOWING_PAGE = """
query UserFollowing($login: String!, $cursor: String!) {
  rateLimit { cost remaining resetAt }
  user(login: $login) {
    following(first: 100, after: $cursor) {
      nodes { login }
      pageInfo { hasNextPage endCursor }
    }
  }
}
"""

_USER_WATCHING_PAGE = """
query UserWatching($login: String!, $cursor: String!) {
  rateLimit { cost remaining resetAt }
  user(login: $login) {
    watching(first: 100, after: $cursor) {
      nodes { nameWithOwner }
      pageInfo { hasNextPage endCursor }
    }
  }
}
"""

# Fallback used when a token lacks `read:org`: drops the `organizations`
# connection (whose `Organization.login` is the scope-gated field that
# otherwise causes GitHub to reject the entire query, leaving users with
# `data: null`).
_USER_QUERY_NO_ORGS = """
query User($login: String!) {
  rateLimit { cost remaining resetAt }
  user(login: $login) {
    login
    name
    databaseId
    followers(first: 100) {
      nodes { login }
      pageInfo { hasNextPage endCursor }
    }
    following(first: 100) {
      nodes { login }
      pageInfo { hasNextPage endCursor }
    }
    starredRepositories(first: 100) {
      nodes { nameWithOwner }
      pageInfo { hasNextPage endCursor }
    }
    watching(first: 100) {
      nodes { nameWithOwner }
      pageInfo { hasNextPage endCursor }
    }
    repositories(
      first: 100,
      ownerAffiliations: OWNER,
      orderBy: {field: UPDATED_AT, direction: DESC}
    ) {
      nodes { nameWithOwner isFork }
      pageInfo { hasNextPage endCursor }
    }
  }
}
"""

_ORG_QUERY = """
query Org($login: String!) {
  rateLimit { cost remaining resetAt }
  organization(login: $login) {
    login
    name
    databaseId
    membersWithRole(first: 100) { nodes { login } }
    repositories(first: 100, orderBy: {field: UPDATED_AT, direction: DESC}) {
      nodes { nameWithOwner isFork }
    }
    teams(first: 100) {
      nodes {
        slug
        name
        description
        privacy
        databaseId
        parentTeam { slug }
        members(first: 100) { nodes { login } }
        repositories(first: 100) { nodes { nameWithOwner } }
      }
    }
  }
}
"""

_REPO_QUERY = """
query Repo(
  $owner: String!,
  $name: String!,
  $issueMax: Int!,
  $prMax: Int!,
  $fetchIssues: Boolean!,
  $fetchPRs: Boolean!
) {
  rateLimit { cost remaining resetAt }
  repository(owner: $owner, name: $name) {
    nameWithOwner
    name
    databaseId
    isFork
    parent { nameWithOwner }
    owner { __typename login }
    issues(first: $issueMax, orderBy: {field: CREATED_AT, direction: DESC})
      @include(if: $fetchIssues) {
      nodes {
        author { login }
        comments(first: 100) { nodes { author { login } } }
      }
    }
    pullRequests(first: $prMax, orderBy: {field: CREATED_AT, direction: DESC})
      @include(if: $fetchPRs) {
      nodes {
        author { login }
        comments(first: 100) { nodes { author { login } } }
        reviews(first: 100) { nodes { author { login } } }
      }
    }
  }
}
"""


def _collect_logins(nodes: Optional[List[Dict[str, Any]]], key: str = "login") -> List[str]:
    out: List[str] = []
    seen: set = set()
    for n in nodes or []:
        if not n:
            continue
        login = n.get(key)
        if not login or login in seen:
            continue
        seen.add(login)
        out.append(login)
    return out


def _collect_actor_logins(nodes: Optional[List[Dict[str, Any]]], field: str) -> List[str]:
    """Collect `nodes[i][field].login`, skipping deleted/null actors."""
    out: List[str] = []
    seen: set = set()
    for n in nodes or []:
        if not n:
            continue
        actor = n.get(field)
        if not actor:
            continue
        login = actor.get("login")
        if not login or login in seen:
            continue
        seen.add(login)
        out.append(login)
    return out


class GitHubGraphQLClient:
    """GraphQL-backed client. Returns cached-shape dicts."""

    def __init__(
        self,
        tokens: List[str],
        cache_dir: Optional[Path] = None,
        crawl_issues: bool = False,
        crawl_prs: bool = False,
        issue_max: int = 100,
        pr_max: int = 100,
        max_per_list: int = 1000,
        max_concurrent_requests: int = 5,
    ):
        if not tokens:
            raise ValueError("At least one GitHub token is required")

        self.tokens = tokens
        self.current_token_idx = 0
        self.crawl_issues = crawl_issues
        self.crawl_prs = crawl_prs
        self.issue_max = issue_max
        self.pr_max = pr_max
        # Cap on how many items we'll paginate per connection (starred,
        # repos). REST returns everything; GraphQL we cap to bound cost.
        self.max_per_list = max_per_list

        # File cache: reuse the same APICache so subsequent runs can skip HTTP.
        # Entries expire per OPC_CACHE_TTL_DAYS (default 30).
        from .github_client import (  # local import to avoid circular dep
            APICache,
            resolve_cache_ttl,
        )
        self.cache = (
            APICache(cache_dir, ttl_seconds=resolve_cache_ttl())
            if cache_dir
            else None
        )

        # Crawler reads .semaphore._value to derive default batch_size.
        self.semaphore = threading.Semaphore(max_concurrent_requests)

        self._http = httpx.Client(timeout=60.0)

        # Token rotation. `_set_token` builds the auth headers for the active
        # token; when its GraphQL point budget is spent we rotate to the next
        # one, and only sleep once every token has been tried and exhausted.
        # `_token_lock` serialises rotation across concurrent worker threads.
        self._token_lock = threading.Lock()
        self._exhausted_streak = 0
        self._set_token(0)

        self.stats: Dict[str, Any] = {
            "graphql_calls": 0,
            "graphql_points": 0,
            "rest_calls": 0,
            "cache_hits": 0,
            "errors": 0,
            "token_switches": 0,
            "rate_limit_waits": 0,
        }
        self._stats_lock = threading.Lock()

        logger.info(
            f"Initialized GraphQL client (crawl_issues={crawl_issues}, "
            f"crawl_prs={crawl_prs}, issue_max={issue_max}, pr_max={pr_max})"
        )

    # ── Token rotation ──────────────────────────────────────────────────────

    def _set_token(self, idx: int) -> None:
        """Point the client at token `idx`, rebuilding both auth headers.

        Headers are reassigned as fresh dicts (not mutated) so a concurrent
        request always reads either the old or the new dict cleanly.
        """
        self.current_token_idx = idx
        token = self.tokens[idx]
        self._headers_graphql = {
            "Authorization": f"bearer {token}",
            "Accept": "application/vnd.github+json",
        }
        self._headers_rest = {
            "Authorization": f"token {token}",
            "Accept": "application/vnd.github+json",
        }

    def _rotate_token(self) -> None:
        """Switch to the next token. The caller must hold `_token_lock`."""
        self._set_token((self.current_token_idx + 1) % len(self.tokens))
        with self._stats_lock:
            self.stats["token_switches"] += 1
        logger.info(
            "GraphQL: rotated to token %d/%d",
            self.current_token_idx + 1,
            len(self.tokens),
        )

    def _sleep_until_reset(self, reset_at: Optional[str]) -> None:
        """Block until the GraphQL rate-limit window resets.

        `reset_at` is ISO-8601 UTC; falls back to 60s if it can't be parsed.
        """
        try:
            import datetime as _dt

            reset = _dt.datetime.fromisoformat((reset_at or "").replace("Z", "+00:00"))
            delay = max(
                0.0, (reset - _dt.datetime.now(_dt.timezone.utc)).total_seconds()
            ) + 5
        except Exception:
            delay = 60.0
        with self._stats_lock:
            self.stats["rate_limit_waits"] += 1
        logger.warning(
            "GraphQL budget exhausted on all %d token(s); sleeping %.0fs",
            len(self.tokens),
            delay,
        )
        time.sleep(delay)

    def _handle_rate_limit(
        self,
        token_idx: int,
        remaining: Optional[int],
        cost: int,
        reset_at: Optional[str],
    ) -> None:
        """React to the `rateLimit` block of a query issued on `token_idx`.

        A healthy budget clears the exhausted-token streak. An exhausted
        token rotates to the next one; once every token has been tried and
        found exhausted, sleep until the window resets. A response from a
        token the client has already rotated past is ignored — a concurrent
        request handled the same exhaustion.
        """
        if remaining is None:
            return
        should_sleep = False
        sleep_reset: Optional[str] = None
        with self._token_lock:
            if token_idx != self.current_token_idx:
                return  # stale: another thread already rotated past this token
            if remaining > 0:
                self._exhausted_streak = 0
                if remaining < 50:
                    logger.warning(
                        "GraphQL points low on token %d/%d: %s remaining "
                        "(query cost %s, resets at %s)",
                        token_idx + 1,
                        len(self.tokens),
                        remaining,
                        cost,
                        reset_at,
                    )
                return
            # remaining <= 0: the active token's GraphQL budget is spent.
            self._exhausted_streak += 1
            if self._exhausted_streak >= len(self.tokens):
                self._exhausted_streak = 0
                should_sleep = True
                sleep_reset = reset_at
            else:
                self._rotate_token()
        # Sleep outside the lock so other workers aren't blocked behind it.
        if should_sleep:
            self._sleep_until_reset(sleep_reset)

    # ── HTTP helpers ────────────────────────────────────────────────────────

    @staticmethod
    def _is_rate_limited(status_code: int, body: Optional[Dict[str, Any]]) -> bool:
        """True when a response means the active token is rate-limited.

        An exhausted token has its query rejected before it runs. Detected by:
          * HTTP 403 / 429, or
          * a GraphQL error whose ``type`` is a rate-limit type, or
          * a GraphQL error whose ``message`` mentions "rate limit".

        The message check is the reliable signal — GitHub's wording varies
        ("RATE_LIMITED" vs "RATE_LIMIT", and some rate-limit errors carry no
        ``type`` at all), but the message always says "rate limit exceeded".
        This is distinct from the graceful case where a query succeeds and
        reports ``remaining: 0``.
        """
        if status_code in (403, 429):
            return True
        for err in (body or {}).get("errors") or []:
            if not err:
                continue
            if str(err.get("type") or "").upper() in ("RATE_LIMITED", "RATE_LIMIT"):
                return True
            if "rate limit" in str(err.get("message") or "").lower():
                return True
        return False

    @staticmethod
    def _reset_hint(resp: "httpx.Response") -> Optional[str]:
        """Best-effort ISO-8601 reset time from rate-limit response headers."""
        reset = resp.headers.get("x-ratelimit-reset")
        if reset:
            try:
                import datetime as _dt

                return _dt.datetime.fromtimestamp(
                    int(reset), _dt.timezone.utc
                ).isoformat()
            except Exception:
                pass
        return None

    def _graphql(self, query: str, variables: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """POST a GraphQL query, rotating tokens to survive rate limits.

        An already-exhausted token rejects a query outright (see
        ``_is_rate_limited``). On that, rotate to the next token and retry;
        once every token has been tried, sleep until the window resets and
        sweep once more before giving up. (``_handle_rate_limit`` is the
        complementary *proactive* path: it rotates after a successful query
        whose ``remaining`` hit 0, so the next query starts on a fresh token.)
        """
        ntok = len(self.tokens)
        for attempt in range(2 * ntok):
            # Capture the active token so the rate-limit handling acts on the
            # token the request actually used, even if a peer worker rotates.
            with self._token_lock:
                headers = self._headers_graphql
                token_idx = self.current_token_idx
            try:
                resp = self._http.post(
                    GRAPHQL_URL,
                    headers=headers,
                    json={"query": query, "variables": variables},
                )
            except httpx.HTTPError as e:
                logger.warning(f"GraphQL transport error: {e}")
                with self._stats_lock:
                    self.stats["errors"] += 1
                return None

            with self._stats_lock:
                self.stats["graphql_calls"] += 1

            body = resp.json() if resp.status_code == 200 else None

            if self._is_rate_limited(resp.status_code, body):
                # The active token is exhausted — it rejected the query before
                # running it. After sweeping every token once, wait for the
                # window to reset, then sweep one more time.
                logger.warning(
                    "GraphQL: token %d/%d rate-limited (attempt %d/%d)",
                    token_idx + 1, ntok, attempt + 1, 2 * ntok,
                )
                if attempt == ntok - 1:
                    self._sleep_until_reset(self._reset_hint(resp))
                with self._token_lock:
                    if token_idx == self.current_token_idx:
                        self._rotate_token()
                continue

            if resp.status_code != 200:
                logger.warning(f"GraphQL HTTP {resp.status_code}: {resp.text[:200]}")
                with self._stats_lock:
                    self.stats["errors"] += 1
                return None

            if body.get("errors"):
                # Classify errors: NOT_FOUND is expected (entity may be a user
                # vs org probe); INSUFFICIENT_SCOPES is a token-config problem
                # the user must fix; everything else is a query bug or
                # transient failure. Log accordingly so silent token-scope
                # mismatches don't hide as "empty graph". Annotate the body so
                # callers can branch without re-parsing the errors list.
                kinds = {e.get("type") for e in body["errors"] if e}
                msgs = "; ".join(
                    str(e.get("message", "")) for e in body["errors"] if e
                )
                body["_error_kinds"] = kinds
                if kinds == {"NOT_FOUND"}:
                    logger.debug(f"GraphQL not-found for {variables}: {msgs}")
                elif "INSUFFICIENT_SCOPES" in kinds:
                    logger.warning(
                        f"GraphQL token scope problem for {variables}: {msgs}"
                    )
                else:
                    logger.warning(f"GraphQL errors for {variables}: {msgs}")

            rl = (body.get("data") or {}).get("rateLimit") or {}
            cost = rl.get("cost", 0) or 0
            with self._stats_lock:
                self.stats["graphql_points"] += cost
            # Proactive: rotate before the next query if this token hit 0.
            self._handle_rate_limit(
                token_idx, rl.get("remaining"), cost, rl.get("resetAt")
            )
            return body

        logger.warning(
            "GraphQL: every token rate-limited even after waiting for reset; "
            "giving up on %s",
            variables,
        )
        with self._stats_lock:
            self.stats["errors"] += 1
        return None

    def _rest_contributors(self, owner: str, name: str, limit: int = 10) -> List[str]:
        """Fetch up to `limit` top contributors via REST. Mirrors REST crawler behavior."""
        try:
            resp = self._http.get(
                f"{REST_BASE}/repos/{owner}/{name}/contributors",
                headers=self._headers_rest,
                params={"per_page": limit, "anon": "false"},
            )
        except httpx.HTTPError as e:
            logger.warning(f"REST contributors error for {owner}/{name}: {e}")
            return []
        with self._stats_lock:
            self.stats["rest_calls"] += 1
        if resp.status_code != 200:
            logger.debug(
                f"REST contributors {owner}/{name} HTTP {resp.status_code}: "
                f"{resp.text[:120]}"
            )
            return []
        return [c.get("login") for c in (resp.json() or []) if c.get("login")][:limit]

    def _paginate_user_connection(
        self,
        page_query: str,
        username: str,
        connection_key: str,
        initial_cursor: str,
        collected_so_far: int,
    ) -> List[Dict[str, Any]]:
        """Walk subsequent pages of a single user connection.

        Caller has already consumed the first page (100 items) and provides
        `initial_cursor` (the `endCursor` of that page) and how many items
        it kept. We follow `pageInfo.endCursor` until either there are no
        more pages or we've collected `self.max_per_list` items in total.
        """
        extra: List[Dict[str, Any]] = []
        cursor = initial_cursor
        total = collected_so_far
        while cursor and total < self.max_per_list:
            body = self._graphql(page_query, {"login": username, "cursor": cursor})
            if body is None:
                break
            connection = ((body.get("data") or {}).get("user") or {}).get(connection_key)
            if not connection:
                break
            nodes = connection.get("nodes") or []
            extra.extend(nodes)
            total += len(nodes)
            page_info = connection.get("pageInfo") or {}
            if not page_info.get("hasNextPage"):
                break
            cursor = page_info.get("endCursor")
        if total >= self.max_per_list:
            logger.info(
                f"GraphQL pagination for {username}.{connection_key} hit "
                f"max_per_list={self.max_per_list}; truncating."
            )
        return extra

    def _rest_user_orgs(self, username: str) -> List[str]:
        """Fetch a user's public organizations via REST.

        GraphQL's `User.organizations` requires `read:org`; REST's
        `/users/{login}/orgs` returns the same public memberships with no
        scope, so we fall back to REST when the GraphQL path is denied.
        """
        try:
            resp = self._http.get(
                f"{REST_BASE}/users/{username}/orgs",
                headers=self._headers_rest,
                params={"per_page": 100},
            )
        except httpx.HTTPError as e:
            logger.warning(f"REST user orgs error for {username}: {e}")
            return []
        with self._stats_lock:
            self.stats["rest_calls"] += 1
        if resp.status_code != 200:
            logger.debug(
                f"REST user orgs {username} HTTP {resp.status_code}: "
                f"{resp.text[:120]}"
            )
            return []
        return [o.get("login") for o in (resp.json() or []) if o.get("login")]

    # ── Public API: get_user / get_organization / get_repository ────────────

    def get_user(self, url: str) -> Optional[Dict[str, Any]]:
        """Get user by canonical URL (e.g. https://github.com/torvalds)."""
        from .node_id import extract_login
        username = extract_login(url)
        cache_key = f"gql_user:{url}"
        if self.cache:
            cached = self.cache.get(cache_key)
            if cached is not None:
                with self._stats_lock:
                    self.stats["cache_hits"] += 1
                return cached

        body = self._graphql(_USER_QUERY, {"login": username})
        if body is None:
            return None
        user = (body.get("data") or {}).get("user")
        # Self-heal: tokens without `read:org` get a null `data` because the
        # `organizations` field's nested `Organization.login` is scope-gated
        # and GitHub rejects the whole query. Retry with a query that drops
        # the organizations connection so basic user fields still come back;
        # `orgs` then gets re-populated via REST (which doesn't need scope
        # for public org memberships).
        used_no_orgs_fallback = False
        if user is None and "INSUFFICIENT_SCOPES" in (body.get("_error_kinds") or set()):
            body = self._graphql(_USER_QUERY_NO_ORGS, {"login": username})
            if body is None:
                return None
            user = (body.get("data") or {}).get("user")
            used_no_orgs_fallback = True
        if not user:
            return None

        # Repositories: paginate if the user has more than the first 100.
        repos_conn = user.get("repositories") or {}
        repo_nodes = list(repos_conn.get("nodes") or [])
        repos_page_info = repos_conn.get("pageInfo") or {}
        if repos_page_info.get("hasNextPage") and repos_page_info.get("endCursor"):
            repo_nodes.extend(self._paginate_user_connection(
                _USER_REPOS_PAGE, username, "repositories",
                repos_page_info["endCursor"], len(repo_nodes),
            ))
        repos = []
        for r in repo_nodes:
            if not r:
                continue
            repos.append({"full_name": r["nameWithOwner"], "fork": r.get("isFork", False)})

        # Starred repositories: paginate similarly.
        starred_conn = user.get("starredRepositories") or {}
        starred_nodes = list(starred_conn.get("nodes") or [])
        starred_page_info = starred_conn.get("pageInfo") or {}
        if starred_page_info.get("hasNextPage") and starred_page_info.get("endCursor"):
            starred_nodes.extend(self._paginate_user_connection(
                _USER_STARRED_PAGE, username, "starredRepositories",
                starred_page_info["endCursor"], len(starred_nodes),
            ))

        # Followers / following / watching: paginate so GraphQL matches REST,
        # which returns the full lists. Bounded by `max_per_list`.
        followers_conn = user.get("followers") or {}
        followers_nodes = list(followers_conn.get("nodes") or [])
        followers_page_info = followers_conn.get("pageInfo") or {}
        if followers_page_info.get("hasNextPage") and followers_page_info.get("endCursor"):
            followers_nodes.extend(self._paginate_user_connection(
                _USER_FOLLOWERS_PAGE, username, "followers",
                followers_page_info["endCursor"], len(followers_nodes),
            ))

        following_conn = user.get("following") or {}
        following_nodes = list(following_conn.get("nodes") or [])
        following_page_info = following_conn.get("pageInfo") or {}
        if following_page_info.get("hasNextPage") and following_page_info.get("endCursor"):
            following_nodes.extend(self._paginate_user_connection(
                _USER_FOLLOWING_PAGE, username, "following",
                following_page_info["endCursor"], len(following_nodes),
            ))

        watching_conn = user.get("watching") or {}
        watching_nodes = list(watching_conn.get("nodes") or [])
        watching_page_info = watching_conn.get("pageInfo") or {}
        if watching_page_info.get("hasNextPage") and watching_page_info.get("endCursor"):
            watching_nodes.extend(self._paginate_user_connection(
                _USER_WATCHING_PAGE, username, "watching",
                watching_page_info["endCursor"], len(watching_nodes),
            ))

        out: Dict[str, Any] = {
            "login": user["login"],
            "name": user.get("name") or "",
            "id": user.get("databaseId") or 0,
            "type": "User",
            "repos": repos,
            "orgs": (
                self._rest_user_orgs(username)
                if used_no_orgs_fallback
                else _collect_logins(
                    (user.get("organizations") or {}).get("nodes", []),
                    key="login",
                )
            ),
            "followers": _collect_logins(followers_nodes, key="login"),
            "following": _collect_logins(following_nodes, key="login"),
            "starred": [
                n["nameWithOwner"]
                for n in starred_nodes
                if n and n.get("nameWithOwner")
            ],
            "watching": [
                n["nameWithOwner"]
                for n in watching_nodes
                if n and n.get("nameWithOwner")
            ],
        }

        if self.cache:
            self.cache.set(cache_key, "", out)
        return out

    def get_organization(self, url: str) -> Optional[Dict[str, Any]]:
        """Get organization by canonical URL (e.g. https://github.com/acme)."""
        from .node_id import extract_login
        org_name = extract_login(url)
        cache_key = f"gql_org:{url}"
        if self.cache:
            cached = self.cache.get(cache_key)
            if cached is not None:
                with self._stats_lock:
                    self.stats["cache_hits"] += 1
                return cached

        body = self._graphql(_ORG_QUERY, {"login": org_name})
        if body is None:
            return None
        org = (body.get("data") or {}).get("organization")
        if not org:
            return None

        repos = []
        for r in (org.get("repositories") or {}).get("nodes", []) or []:
            if not r:
                continue
            repos.append({"full_name": r["nameWithOwner"], "fork": r.get("isFork", False)})

        teams_dicts: List[Dict[str, Any]] = []
        teams_payload = (org.get("teams") or {}).get("nodes", []) or []
        for t in teams_payload:
            if not t:
                continue
            parent_obj = t.get("parentTeam") or {}
            teams_dicts.append({
                "slug": t["slug"],
                "name": t.get("name") or "",
                "description": t.get("description") or "",
                "privacy": t.get("privacy") or "",
                "id": t.get("databaseId") or 0,
                "parent_slug": parent_obj.get("slug") if parent_obj else None,
                "members": _collect_logins(
                    (t.get("members") or {}).get("nodes", []),
                    key="login",
                ),
                "repositories": [
                    n["nameWithOwner"]
                    for n in (t.get("repositories") or {}).get("nodes", []) or []
                    if n and n.get("nameWithOwner")
                ],
            })

        out: Dict[str, Any] = {
            "login": org["login"],
            "name": org.get("name") or "",
            "id": org.get("databaseId") or 0,
            "type": "Organization",
            "members": _collect_logins(
                (org.get("membersWithRole") or {}).get("nodes", []),
                key="login",
            ),
            "repos": repos,
            "teams": teams_dicts,
        }

        if self.cache:
            self.cache.set(cache_key, "", out)
        return out

    def get_repository(self, url: str) -> Optional[Dict[str, Any]]:
        """Get repository by canonical URL (e.g. https://github.com/acme/widget)."""
        from .node_id import extract_full_name
        repo_full_name = extract_full_name(url)
        cache_key = f"gql_repo:{url}:{int(self.crawl_issues)}:{int(self.crawl_prs)}:{self.issue_max}:{self.pr_max}"
        if self.cache:
            cached = self.cache.get(cache_key)
            if cached is not None:
                with self._stats_lock:
                    self.stats["cache_hits"] += 1
                return cached

        if "/" not in repo_full_name:
            return None
        owner, name = repo_full_name.split("/", 1)

        body = self._graphql(_REPO_QUERY, {
            "owner": owner,
            "name": name,
            "issueMax": self.issue_max,
            "prMax": self.pr_max,
            "fetchIssues": self.crawl_issues,
            "fetchPRs": self.crawl_prs,
        })
        if body is None:
            return None
        repo = (body.get("data") or {}).get("repository")
        if not repo:
            return None

        contributors = self._rest_contributors(owner, name, limit=10)

        issue_authors: List[str] = []
        commenters: List[str] = []
        if self.crawl_issues:
            issue_nodes = (repo.get("issues") or {}).get("nodes", []) or []
            issue_authors = _collect_actor_logins(issue_nodes, "author")
            seen_c: set = set()
            for n in issue_nodes:
                for c in (n.get("comments") or {}).get("nodes", []) or []:
                    actor = (c or {}).get("author") or {}
                    login = actor.get("login")
                    if login and login not in seen_c:
                        seen_c.add(login)
                        commenters.append(login)

        pr_authors: List[str] = []
        pr_reviewers: List[str] = []
        if self.crawl_prs:
            pr_nodes = (repo.get("pullRequests") or {}).get("nodes", []) or []
            pr_authors = _collect_actor_logins(pr_nodes, "author")
            seen_c2: set = set(commenters)
            seen_r: set = set()
            for n in pr_nodes:
                for c in (n.get("comments") or {}).get("nodes", []) or []:
                    actor = (c or {}).get("author") or {}
                    login = actor.get("login")
                    if login and login not in seen_c2:
                        seen_c2.add(login)
                        commenters.append(login)
                for r in (n.get("reviews") or {}).get("nodes", []) or []:
                    actor = (r or {}).get("author") or {}
                    login = actor.get("login")
                    if login and login not in seen_r:
                        seen_r.add(login)
                        pr_reviewers.append(login)

        owner_obj = repo.get("owner") or {}
        out: Dict[str, Any] = {
            "full_name": repo["nameWithOwner"],
            "name": repo.get("name") or repo["nameWithOwner"].split("/", 1)[1],
            "id": repo.get("databaseId") or 0,
            "owner": owner_obj.get("login", owner),
            "owner_type": owner_obj.get("__typename", "User"),
            "is_fork": repo.get("isFork", False),
            "parent": (repo.get("parent") or {}).get("nameWithOwner"),
            "contributors": contributors,
            "issue_authors": issue_authors,
            "pr_authors": pr_authors,
            "commenters": commenters,
            "pr_reviewers": pr_reviewers,
        }

        if self.cache:
            self.cache.set(cache_key, "", out)
        return out

    # ── Compatibility stubs ─────────────────────────────────────────────────

    def _make_request(self, func, *args, **kwargs):
        """Compatibility shim: the crawler invokes this on live-path branches
        (which never run when our get_* methods always return dicts). If
        anything does reach here, fall through to a direct call so behavior
        degrades gracefully rather than crashing."""
        try:
            return func(*args, **kwargs)
        except Exception as e:
            logger.warning(f"GraphQL client _make_request shim failed: {e}")
            return None

    def get_stats(self) -> Dict[str, Any]:
        stats = dict(self.stats)
        stats["efficiency"] = {
            "cache_hit_rate": (
                self.stats["cache_hits"]
                / max(1, self.stats["graphql_calls"] + self.stats["cache_hits"])
                * 100
            ),
            "points_per_call": (
                self.stats["graphql_points"] / max(1, self.stats["graphql_calls"])
            ),
        }
        # Mirror REST client field names the CLI table reads.
        stats.setdefault("api_calls", self.stats["graphql_calls"] + self.stats["rest_calls"])
        stats.setdefault("rate_limit_waits", 0)
        stats.setdefault("token_switches", 0)
        stats.setdefault("throttle_waits", 0)
        return stats
