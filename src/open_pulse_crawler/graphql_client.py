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
    followers(first: 100) { nodes { login } }
    following(first: 100) { nodes { login } }
    starredRepositories(first: 100) { nodes { nameWithOwner } }
    watching(first: 100) { nodes { nameWithOwner } }
    organizations(first: 100) { nodes { login } }
    repositories(
      first: 100,
      ownerAffiliations: OWNER,
      orderBy: {field: UPDATED_AT, direction: DESC}
    ) {
      nodes { nameWithOwner isFork }
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

        # File cache: reuse the same APICache so subsequent runs can skip HTTP.
        from .github_client import APICache  # local import to avoid circular dep
        self.cache = APICache(cache_dir) if cache_dir else None

        # Crawler reads .semaphore._value to derive default batch_size.
        self.semaphore = threading.Semaphore(max_concurrent_requests)

        self._http = httpx.Client(timeout=60.0)
        self._headers_graphql = {
            "Authorization": f"bearer {tokens[0]}",
            "Accept": "application/vnd.github+json",
        }
        self._headers_rest = {
            "Authorization": f"token {tokens[0]}",
            "Accept": "application/vnd.github+json",
        }

        self.stats: Dict[str, Any] = {
            "graphql_calls": 0,
            "graphql_points": 0,
            "rest_calls": 0,
            "cache_hits": 0,
            "errors": 0,
        }
        self._stats_lock = threading.Lock()

        logger.info(
            f"Initialized GraphQL client (crawl_issues={crawl_issues}, "
            f"crawl_prs={crawl_prs}, issue_max={issue_max}, pr_max={pr_max})"
        )

    # ── HTTP helpers ────────────────────────────────────────────────────────

    def _graphql(self, query: str, variables: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        try:
            resp = self._http.post(
                GRAPHQL_URL,
                headers=self._headers_graphql,
                json={"query": query, "variables": variables},
            )
        except httpx.HTTPError as e:
            logger.warning(f"GraphQL transport error: {e}")
            with self._stats_lock:
                self.stats["errors"] += 1
            return None

        with self._stats_lock:
            self.stats["graphql_calls"] += 1

        if resp.status_code != 200:
            logger.warning(f"GraphQL HTTP {resp.status_code}: {resp.text[:200]}")
            with self._stats_lock:
                self.stats["errors"] += 1
            return None

        body = resp.json()
        if body.get("errors"):
            # Classify errors: NOT_FOUND is expected (entity may be a user vs
            # org probe); INSUFFICIENT_SCOPES is a token-config problem the
            # user must fix; everything else is a query bug or transient
            # failure. Log accordingly so silent token-scope mismatches don't
            # hide as "empty graph".
            kinds = {e.get("type") for e in body["errors"] if e}
            msgs = "; ".join(
                str(e.get("message", "")) for e in body["errors"] if e
            )
            if kinds == {"NOT_FOUND"}:
                logger.debug(f"GraphQL not-found for {variables}: {msgs}")
            elif "INSUFFICIENT_SCOPES" in kinds:
                logger.error(
                    f"GraphQL token scope problem for {variables}: {msgs}"
                )
            else:
                logger.warning(f"GraphQL errors for {variables}: {msgs}")
        rl = (body.get("data") or {}).get("rateLimit") or {}
        cost = rl.get("cost", 0) or 0
        remaining = rl.get("remaining")
        with self._stats_lock:
            self.stats["graphql_points"] += cost
        if remaining is not None and remaining < 50:
            logger.warning(
                f"GraphQL points remaining low: {remaining}; query cost {cost}; "
                f"resets at {rl.get('resetAt')}"
            )
            if remaining <= 0:
                # Sleep until reset (`resetAt` is ISO-8601 UTC).
                try:
                    import datetime as _dt
                    reset_at = _dt.datetime.fromisoformat(
                        (rl.get("resetAt") or "").replace("Z", "+00:00")
                    )
                    delay = max(0.0, (reset_at - _dt.datetime.now(_dt.timezone.utc)).total_seconds()) + 5
                    logger.warning(f"GraphQL budget exhausted; sleeping {delay:.0f}s")
                    time.sleep(delay)
                except Exception:
                    time.sleep(60)
        return body

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

    # ── Public API: get_user / get_organization / get_repository ────────────

    def get_user(self, username: str) -> Optional[Dict[str, Any]]:
        cache_key = f"gql_user:{username}"
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
        if not user:
            return None

        repos = []
        for r in (user.get("repositories") or {}).get("nodes", []) or []:
            if not r:
                continue
            repos.append({"full_name": r["nameWithOwner"], "fork": r.get("isFork", False)})

        out: Dict[str, Any] = {
            "login": user["login"],
            "name": user.get("name") or "",
            "id": user.get("databaseId") or 0,
            "type": "User",
            "repos": repos,
            "orgs": _collect_logins(
                (user.get("organizations") or {}).get("nodes", []),
                key="login",
            ),
            "followers": _collect_logins(
                (user.get("followers") or {}).get("nodes", []),
                key="login",
            ),
            "following": _collect_logins(
                (user.get("following") or {}).get("nodes", []),
                key="login",
            ),
            "starred": [
                n["nameWithOwner"]
                for n in (user.get("starredRepositories") or {}).get("nodes", []) or []
                if n and n.get("nameWithOwner")
            ],
            "watching": [
                n["nameWithOwner"]
                for n in (user.get("watching") or {}).get("nodes", []) or []
                if n and n.get("nameWithOwner")
            ],
        }

        if self.cache:
            self.cache.set(cache_key, "", out)
        return out

    def get_organization(self, org_name: str) -> Optional[Dict[str, Any]]:
        cache_key = f"gql_org:{org_name}"
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

    def get_repository(self, repo_full_name: str) -> Optional[Dict[str, Any]]:
        cache_key = f"gql_repo:{repo_full_name}:{int(self.crawl_issues)}:{int(self.crawl_prs)}:{self.issue_max}:{self.pr_max}"
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
