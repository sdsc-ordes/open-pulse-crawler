"""Core crawler logic implementing BFS strategy."""

import logging
from typing import List, Set, Dict, Tuple, Optional, Callable
from pathlib import Path
import json
from collections import deque
from datetime import datetime
from tqdm import tqdm
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading

from .models import (
    GraphData, UserModel, OrgModel, RepoModel, TeamModel,
    GitHubItemType,
)
from .platforms.github import GitHubClient
from .dependency_utils import fetch_dependencies_sbom, fetch_dependents
from .gimie_client import GimieJsonLdClient
from .gimie_jsonld import parse_gimie_repo_jsonld
from .node_id import (
    NodeKind,
    extract_full_name,
    extract_login,
    parse_seed as parse_seed_url,
    repo_url,
    team_url,
    user_url,
)


# Bumped alongside models.GRAPH_SCHEMA_VERSION whenever the on-disk
# state.json layout changes incompatibly. Old state files are refused
# on load with a clear error.
STATE_SCHEMA_VERSION = 2


def _kind_to_url(kind: str, identifier: str) -> str:
    """Convert a (kind, bare-identifier) pair into a canonical URL.

    ``kind`` is one of the strings the crawler queues with: ``"user"``,
    ``"org"``, ``"user_or_org"``, ``"repo"``, or ``"team"``. The
    identifier shape matches the kind — login for users / orgs,
    ``owner/repo`` for repos, ``org/slug`` for teams.

    Idempotent on values that are already canonical URLs, so call sites
    can pass either form during transition.
    """
    if identifier.startswith("https://") or identifier.startswith("http://"):
        return parse_seed_url(identifier)[1]
    if kind == "repo":
        return repo_url(identifier)
    if kind == "team":
        org, slug = identifier.split("/", 1)
        return team_url(org, slug)
    # user, org, user_or_org all map to a one-segment login URL.
    return user_url(identifier)

logger = logging.getLogger(__name__)


class GitHubCrawler:
    """BFS crawler for GitHub entities."""
    
    def __init__(
        self,
        client: GitHubClient,
        max_rounds: int = 3,
        state_file: Optional[Path] = None,
        batch_size: Optional[int] = None,
        crawl_dependencies: bool = False,
        crawl_dependents: bool = False,
        crawl_issues: bool = False,
        crawl_prs: bool = False,
        issue_max: int = 100,
        pr_max: int = 100,
        min_stars: int = 0,
        max_dependents: Optional[int] = None,
        max_contributors: Optional[int] = None,
        gimie_repos: bool = False,
        gimie_api_base: str = "http://host.docker.internal:1234",
        gimie_store_jsonld_dir: Optional[Path] = None,
        gimie_skip_existing_jsonld: bool = False,
    ):
        """
        Initialize the crawler.

        Args:
            client: GitHub API client
            max_rounds: Maximum number of BFS rounds
            state_file: File to save/load crawler state
            batch_size: Number of nodes to process concurrently (default: matches client's max_concurrent_requests)
            crawl_dependencies: Whether to crawl dependencies (downstream)
            crawl_dependents: Whether to crawl dependents (upstream)
            crawl_issues: Whether to fetch issue authors and conversation commenters per repo
            crawl_prs: Whether to fetch PR authors, conversation commenters, and reviewers per repo
            issue_max: Maximum number of issues to scan per repo (most recent)
            pr_max: Maximum number of PRs to scan per repo (most recent)
            min_stars: Minimum stars for dependents/dependencies filtering
            max_contributors: Optional per-repo contributor limit. When set,
                at most this many contributors are recorded and queued per
                repo — a repo with more is truncated to the top N, never
                skipped. ``None`` (the default) means no cap: every
                contributor the GitHub API returns is recorded and queued.
            gimie_repos: When true, populate repository nodes from gimie JSON-LD.
            gimie_api_base: Base URL for the gimie JSON-LD API.
            gimie_store_jsonld_dir: Optional directory to store gimie JSON-LD payloads.
            gimie_skip_existing_jsonld: If storing, skip HTTP when payload file exists under jsonld_dir.
        """
        self.client = client
        self.max_rounds = max_rounds
        self.state_file = state_file
        self.batch_size = batch_size if batch_size is not None else client.semaphore._value
        self.crawl_dependencies = crawl_dependencies
        self.crawl_dependents = crawl_dependents
        self.crawl_issues = crawl_issues
        self.crawl_prs = crawl_prs
        self.issue_max = issue_max
        self.pr_max = pr_max
        self.min_stars = min_stars
        self.max_dependents = max_dependents
        self.max_contributors = max_contributors

        # Optional gimie hybrid repo population.
        self.gimie_repos = gimie_repos
        self.gimie_client: Optional[GimieJsonLdClient] = None
        if self.gimie_repos:
            self.gimie_client = GimieJsonLdClient(
                api_base=gimie_api_base,
                jsonld_repo_segment="gimie",
                jsonld_dir=gimie_store_jsonld_dir,
                skip_existing_jsonld=gimie_skip_existing_jsonld,
            )
        
        # Graph data
        self.graph = GraphData()
        
        # BFS tracking
        self.current_round = 0
        self.visited: Set[str] = set()
        self.seed_nodes: Set[str] = set()
        self.queue: deque = deque()
        
        # Track discovered (but not yet explored) nodes for visualization
        # Maps: node_id -> (node_type, parent_id, parent_type)
        self.discovered_nodes: Dict[str, tuple] = {}
        
        # Thread-safe access to graph and visited set
        self.graph_lock = threading.Lock()
        self.visited_lock = threading.Lock()

        # Cooperative pause / cancel flags. The HTTP API toggles these via
        # the JobRecord; the BFS loop checks them between rounds and at
        # the head of each batch to honour Pause/Cancel without ripping
        # work mid-flight (network calls / thread-pool tasks finish first).
        self.pause_requested: bool = False
        self.cancel_requested: bool = False
        
        # Statistics per round
        self.round_stats: List[Dict] = []
        
        # Optional callback for incremental exports
        self.incremental_export_callback: Optional[Callable[[int], None]] = None

        logger.info(f"Crawler initialized with batch_size={self.batch_size}")

    def _contributor_limit(self) -> Optional[int]:
        """How many contributors to take per repo, or None for no limit.

        `max_contributors`, when set, is a take-up-to-N limit: a repo with
        more contributors is truncated to the top N — never skipped. When
        unset (the default), there is no cap — every contributor the GitHub
        API returns is recorded and queued. (GitHub itself caps the
        contributors endpoint at ~500 for very large repos.)

        Returns an int usable as a slice bound; `None` slices the whole list.
        """
        return self.max_contributors

    def _track_discovered_node(self, node_type: str, url: str, parent_url: str = None, parent_type: str = None):
        """
        Track a discovered but not-yet-explored node for visualization purposes.
        This does NOT add it to the main graph (only explored nodes go there).
        Thread-safe with visited_lock.

        Args:
            node_type: Type of node ('user', 'org', 'repo').
            url: Canonical URL of the node — the same key under which it
                will live in :class:`GraphData` once explored.
            parent_url: URL of the parent node that discovered this node.
            parent_type: Type of parent node ('user', 'org', 'repo').
        """
        if url in self.visited:
            return
        is_in_graph = (
            (node_type == 'user' and url in self.graph.users) or
            (node_type == 'org' and url in self.graph.orgs) or
            (node_type == 'repo' and url in self.graph.repos)
        )
        if not is_in_graph and url not in self.discovered_nodes:
            self.discovered_nodes[url] = (node_type, parent_url, parent_type)
    
    def save_state(self):
        """Save crawler state to file."""
        if not self.state_file:
            return

        state = {
            'schema_version': STATE_SCHEMA_VERSION,
            'current_round': self.current_round,
            'visited': list(self.visited),
            'seed_nodes': list(self.seed_nodes),
            'queue': list(self.queue),
            'graph': self.graph.model_dump(),
            'round_stats': self.round_stats,
        }

        try:
            with open(self.state_file, 'w') as f:
                json.dump(state, f, indent=2)
            logger.info(f"State saved to {self.state_file}")
        except Exception as e:
            logger.error(f"Failed to save state: {e}")

    def load_state(self) -> bool:
        """Load crawler state from file.

        Refuses to load files written under an earlier ``schema_version``
        — that's the agreed hard break for the URL-keyed-nodes cutover
        (see node_id + models.GRAPH_SCHEMA_VERSION). The caller decides
        whether to re-crawl from seeds or fail.

        Returns:
            True if state was loaded successfully.
        """
        if not self.state_file or not self.state_file.exists():
            return False

        try:
            with open(self.state_file, 'r') as f:
                state = json.load(f)

            file_version = state.get('schema_version', 1)
            if file_version != STATE_SCHEMA_VERSION:
                logger.error(
                    "State file %s has schema_version=%s but this build "
                    "requires version %s. Re-crawl from seeds.",
                    self.state_file, file_version, STATE_SCHEMA_VERSION,
                )
                return False

            self.current_round = state.get('current_round', 0)
            self.visited = set(state.get('visited', []))
            self.seed_nodes = set(state.get('seed_nodes', []))
            self.queue = deque(state.get('queue', []))
            self.graph = GraphData(**state.get('graph', {}))
            self.round_stats = state.get('round_stats', [])

            logger.info(f"State loaded from {self.state_file}")
            logger.info(f"Resuming from round {self.current_round}")
            return True
        except Exception as e:
            logger.error(f"Failed to load state: {e}")
            return False
    
    def _parse_seed(self, seed: str) -> Tuple[str, str]:
        """Parse a seed string into (kind, canonical_url).

        Accepts a bare login, ``owner/repo``, or a full GitHub URL.
        Delegates to :func:`open_pulse_crawler.node_id.parse_seed` for
        normalization; the returned ``kind`` is mapped onto the
        crawler's own type strings (``"user_or_org"`` / ``"repo"`` /
        ``"team"``) for compatibility with the queue tuple shape.
        """
        kind, url = parse_seed_url(seed)
        if kind == NodeKind.REPO:
            return ('repo', url)
        if kind == NodeKind.TEAM:
            return ('team', url)
        return ('user_or_org', url)

    def add_seeds(self, seeds: List[str]):
        """Add initial seed nodes to the queue (keyed by canonical URL)."""
        for seed in seeds:
            seed_type, url = self._parse_seed(seed)
            if url not in self.visited:
                self.queue.append((seed_type, url, 0))  # (type, url, round)
                self.seed_nodes.add(url)
        logger.info(f"Added {len(seeds)} seed nodes")
    
    def _process_user(self, user_url_value: str) -> Optional[UserModel]:
        """Process a user (by canonical URL) and return UserModel."""
        username = extract_login(user_url_value)
        try:
            user_obj = self.client.get_user(user_url_value)
            if not user_obj:
                return None
            
            # Handle cached data (dict) vs live API object
            is_cached = isinstance(user_obj, dict)
            
            if is_cached:
                # Check if this is actually an organization (from cached data)
                if user_obj.get('type') == 'Organization':
                    logger.debug(f"{username} is an organization, not a user")
                    return None
                
                user_type_str = user_obj.get('type', 'User')
                user_type = GitHubItemType.BOT if user_type_str == 'Bot' else GitHubItemType.USER
                    
                user = UserModel(
                    login=user_obj['login'],
                    name=user_obj.get('name', ''),
                    id=user_obj.get('id', 0),
                    type=user_type,
                    is_explored=True,
                    exploration_timestamp=datetime.now().isoformat(),
                )
                
                # Use cached repos data if available
                cached_repos = user_obj.get('repos', [])
                repos_to_queue = []
                for repo_data in cached_repos:
                    if repo_data.get('fork'):
                        user.forked_repositories.append(repo_data['full_name'])
                    else:
                        user.authored_repositories.append(repo_data['full_name'])
                    
                    repos_to_queue.append(repo_data['full_name'])
                
                # Use cached organizations data if available
                cached_orgs = user_obj.get('orgs', [])
                orgs_to_queue = list(cached_orgs)

                # Record follow lists (no queueing — edges only).
                user.followers.extend(user_obj.get('followers', []))
                user.following.extend(user_obj.get('following', []))

                # Record star/watch lists (no queueing — edges only).
                user.starred_repositories.extend(user_obj.get('starred', []))
                user.watched_repositories.extend(user_obj.get('watching', []))

                # Add all items to queue in a single lock acquisition (URLs).
                with self.visited_lock:
                    for repo_name in repos_to_queue:
                        ru = repo_url(repo_name)
                        if ru not in self.visited:
                            self.queue.append(('repo', ru, self.current_round + 1))
                            self._track_discovered_node('repo', ru, user_url_value, 'user')
                    for org_login in orgs_to_queue:
                        ou = user_url(org_login)
                        if ou not in self.visited:
                            self.queue.append(('org', ou, self.current_round + 1))
                            self._track_discovered_node('org', ou, user_url_value, 'user')
            else:
                # Check if this is actually an organization
                if user_obj.type == 'Organization':
                    logger.debug(f"{username} is an organization, not a user")
                    return None
                
                user = UserModel(
                    login=user_obj.login,
                    name=user_obj.name or '',
                    id=user_obj.id,
                    type=GitHubItemType.USER if user_obj.type == 'User' else GitHubItemType.BOT,
                    is_explored=True,
                    exploration_timestamp=datetime.now().isoformat(),
                )
                
                # Get user's repositories from live API
                repos_to_queue = []
                try:
                    repos = self.client._make_request(user_obj.get_repos)
                    for repo in repos:
                        if repo.fork:
                            user.forked_repositories.append(repo.full_name)
                        else:
                            user.authored_repositories.append(repo.full_name)
                        repos_to_queue.append(repo.full_name)
                except Exception as e:
                    logger.warning(f"Failed to get repos for user {username}: {e}")
                
                # Get user's organization memberships from live API
                orgs_to_queue = []
                try:
                    orgs = self.client._make_request(user_obj.get_orgs)
                    orgs_to_queue = [org.login for org in orgs]
                except Exception as e:
                    logger.warning(f"Failed to get organizations for user {username}: {e}")

                # Fetch follow lists from live API — record only, do not queue.
                try:
                    followers = self.client._make_request(user_obj.get_followers)
                    user.followers.extend(f.login for f in followers)
                except Exception as e:
                    logger.warning(f"Failed to get followers for user {username}: {e}")

                try:
                    following = self.client._make_request(user_obj.get_following)
                    user.following.extend(f.login for f in following)
                except Exception as e:
                    logger.warning(f"Failed to get following for user {username}: {e}")

                # Fetch starred and watched (subscriptions) — record only, do not queue.
                try:
                    starred = self.client._make_request(user_obj.get_starred)
                    user.starred_repositories.extend(r.full_name for r in starred)
                except Exception as e:
                    logger.warning(f"Failed to get starred for user {username}: {e}")

                try:
                    subs = self.client._make_request(user_obj.get_subscriptions)
                    user.watched_repositories.extend(r.full_name for r in subs)
                except Exception as e:
                    logger.warning(f"Failed to get subscriptions for user {username}: {e}")

                # Add all items to queue in a single lock acquisition (URLs).
                with self.visited_lock:
                    for repo_name in repos_to_queue:
                        ru = repo_url(repo_name)
                        if ru not in self.visited:
                            self.queue.append(('repo', ru, self.current_round + 1))
                            self._track_discovered_node('repo', ru, user_url_value, 'user')
                    for org_login in orgs_to_queue:
                        ou = user_url(org_login)
                        if ou not in self.visited:
                            self.queue.append(('org', ou, self.current_round + 1))
                            self._track_discovered_node('org', ou, user_url_value, 'user')

            return user
        except Exception as e:
            logger.error(f"Error processing user {username}: {e}")
            return None
    
    def _process_organization(self, org_url_value: str) -> Optional[OrgModel]:
        """Process an organization (by canonical URL) and return OrgModel."""
        org_name = extract_login(org_url_value)
        try:
            org_obj = self.client.get_organization(org_url_value)
            if not org_obj:
                return None
            
            is_cached = isinstance(org_obj, dict)
            
            if is_cached:
                org = OrgModel(
                    login=org_obj['login'],
                    name=org_obj.get('name', ''),
                    id=org_obj.get('id', 0),
                    type=GitHubItemType.ORGANIZATION,
                    is_explored=True,
                    exploration_timestamp=datetime.now().isoformat(),
                )
                
                # Use cached members data if available
                cached_members = org_obj.get('members', [])
                org.members.extend(cached_members)
                members_to_queue = list(cached_members)
                
                # Use cached repos data if available
                cached_repos = org_obj.get('repos', [])
                repos_to_queue = []
                for repo_data in cached_repos:
                    if repo_data.get('fork'):
                        org.forked_repositories.append(repo_data['full_name'])
                    else:
                        org.authored_repositories.append(repo_data['full_name'])
                    repos_to_queue.append(repo_data['full_name'])
                
                # Cached teams (populated by GraphQL client; REST cache omits teams).
                for team_data in org_obj.get('teams', []) or []:
                    self._build_team_from_dict(org_name, team_data)

                # Add all items to queue in a single lock acquisition (URLs).
                with self.visited_lock:
                    for member_login in members_to_queue:
                        mu = user_url(member_login)
                        if mu not in self.visited:
                            self.queue.append(('user', mu, self.current_round + 1))
                            self._track_discovered_node('user', mu, org_url_value, 'org')
                    for repo_name in repos_to_queue:
                        ru = repo_url(repo_name)
                        if ru not in self.visited:
                            self.queue.append(('repo', ru, self.current_round + 1))
                            self._track_discovered_node('repo', ru, org_url_value, 'org')
            else:
                org = OrgModel(
                    login=org_obj.login,
                    name=org_obj.name or '',
                    id=org_obj.id,
                    type=GitHubItemType.ORGANIZATION,
                    is_explored=True,
                    exploration_timestamp=datetime.now().isoformat(),
                )
                
                # Get organization members from live API
                members_to_queue = []
                try:
                    members = self.client._make_request(org_obj.get_members)
                    for member in members:
                        org.members.append(member.login)
                        members_to_queue.append(member.login)
                except Exception as e:
                    logger.warning(f"Failed to get members for org {org_name}: {e}")
                
                # Get organization repositories from live API
                repos_to_queue = []
                try:
                    repos = self.client._make_request(org_obj.get_repos)
                    for repo in repos:
                        if repo.fork:
                            org.forked_repositories.append(repo.full_name)
                        else:
                            org.authored_repositories.append(repo.full_name)
                        repos_to_queue.append(repo.full_name)
                except Exception as e:
                    logger.warning(f"Failed to get repos for org {org_name}: {e}")

                # Get organization teams from live API. Requires the auth token
                # to be an org member with team-read perms; 403/404 is expected
                # for external orgs and is logged at debug level.
                self._fetch_org_teams(org_obj, org_name)

                # Add all items to queue in a single lock acquisition (URLs).
                with self.visited_lock:
                    for member_login in members_to_queue:
                        mu = user_url(member_login)
                        if mu not in self.visited:
                            self.queue.append(('user', mu, self.current_round + 1))
                            self._track_discovered_node('user', mu, org_url_value, 'org')
                    for repo_name in repos_to_queue:
                        ru = repo_url(repo_name)
                        if ru not in self.visited:
                            self.queue.append(('repo', ru, self.current_round + 1))
                            self._track_discovered_node('repo', ru, org_url_value, 'org')

            return org
        except Exception as e:
            logger.error(f"Error processing organization {org_name}: {e}")
            return None

    def _build_team_from_dict(self, org_name: str, team_data: Dict):
        """Build a TeamModel from a dict (e.g. served by the GraphQL client)."""
        try:
            slug = team_data["slug"]
            full_name = f"{org_name}/{slug}"
            parent_slug = team_data.get("parent_slug")
            parent_full_name = f"{org_name}/{parent_slug}" if parent_slug else None

            team = TeamModel(
                full_name=full_name,
                slug=slug,
                name=team_data.get("name") or "",
                id=team_data.get("id", 0),
                org=org_name,
                description=team_data.get("description") or "",
                privacy=team_data.get("privacy") or "",
                parent=parent_full_name,
                is_explored=True,
                exploration_timestamp=datetime.now().isoformat(),
            )
            team.members.extend(team_data.get("members", []) or [])
            team.repositories.extend(team_data.get("repositories", []) or [])

            with self.graph_lock:
                self.graph.add_team(team)
        except Exception as e:
            logger.warning(f"Failed to materialize team from dict for org {org_name}: {e}")

    def _fetch_org_teams(self, org_obj, org_name: str):
        """Fetch teams for an org from the live API and add them to the graph.

        Teams require auth-token membership in the org. When access is denied
        we log at debug level and move on — this is the common case for
        externally-crawled orgs.
        """
        try:
            teams = self.client._make_request(org_obj.get_teams)
            if teams is None:
                return
            team_objs = list(teams)
        except Exception as e:
            logger.debug(f"Cannot list teams for org {org_name} (likely no access): {e}")
            return

        for team_obj in team_objs:
            try:
                slug = team_obj.slug
                full_name = f"{org_name}/{slug}"
                parent_full_name = None
                if getattr(team_obj, "parent", None) is not None:
                    parent_full_name = f"{org_name}/{team_obj.parent.slug}"

                team = TeamModel(
                    full_name=full_name,
                    slug=slug,
                    name=team_obj.name or "",
                    id=team_obj.id,
                    org=org_name,
                    description=team_obj.description or "",
                    privacy=getattr(team_obj, "privacy", "") or "",
                    parent=parent_full_name,
                    is_explored=True,
                    exploration_timestamp=datetime.now().isoformat(),
                )

                try:
                    t_members = self.client._make_request(team_obj.get_members)
                    team.members.extend(m.login for m in t_members)
                except Exception as e:
                    logger.warning(f"Failed to get members for team {full_name}: {e}")

                try:
                    t_repos = self.client._make_request(team_obj.get_repos)
                    team.repositories.extend(r.full_name for r in t_repos)
                except Exception as e:
                    logger.warning(f"Failed to get repos for team {full_name}: {e}")

                with self.graph_lock:
                    self.graph.add_team(team)
            except Exception as e:
                logger.warning(f"Failed to process team in org {org_name}: {e}")
    
    def _process_repository(self, repo_url_value: str) -> Optional[RepoModel]:
        """Process a repository (by canonical URL) and return RepoModel."""
        repo_full_name = extract_full_name(repo_url_value)
        try:
            # ── Optional gimie hybrid repo processing ─────────────────────
            if self.gimie_repos and self.gimie_client is not None:
                try:
                    payload = self.gimie_client.fetch_repo_jsonld(repo_full_name)
                    if payload is not None:
                        parsed = parse_gimie_repo_jsonld(payload)
                        repo_name = parsed.repo_full_name.split("/", 1)[1]

                        repo = RepoModel(
                            full_name=parsed.repo_full_name,
                            name=repo_name,
                            id=0,
                            type=GitHubItemType.REPOSITORY,
                            owner=parsed.owner_login,
                            is_fork=False,
                            forked_from=None,
                            is_explored=True,
                            exploration_timestamp=datetime.now().isoformat(),
                        )

                        # All contributors, or the top N if max_contributors is set.
                        gimie_contributors = parsed.contributor_logins[
                            : self._contributor_limit()
                        ]
                        repo.contributors.extend(gimie_contributors)

                        items_to_queue: List[tuple] = []

                        # Add owner node (org/user), but don't fail if type is missing.
                        owner_kind = parsed.login_type_map.get(parsed.owner_login)
                        owner_node_type = (
                            "org"
                            if owner_kind == "org"
                            else "user"
                            if owner_kind == "user"
                            else "user_or_org"
                        )
                        items_to_queue.append((owner_node_type, parsed.owner_login))

                        # Add contributors (same take-up-to-N limit).
                        for contributor_login in gimie_contributors:
                            kind = parsed.login_type_map.get(contributor_login)
                            node_type = (
                                "org"
                                if kind == "org"
                                else "user"
                                if kind == "user"
                                else "user_or_org"
                            )
                            items_to_queue.append((node_type, contributor_login))

                        # Crawl dependencies/dependents using existing logic (SBOM + "Used by").
                        if self.crawl_dependencies:
                            try:
                                cache_endpoint = f"sbom/{repo_full_name}"
                                cached_deps = (
                                    self.client.cache.get(cache_endpoint)
                                    if self.client.cache
                                    else None
                                )

                                if cached_deps is not None:
                                    dependencies = cached_deps
                                    logger.debug(f"Cache hit for dependencies of {repo_full_name}")
                                else:
                                    token = self.client.tokens[self.client.current_token_idx]
                                    dependencies = fetch_dependencies_sbom(repo_full_name, token)
                                    if dependencies is not None and self.client.cache:
                                        self.client.cache.set(cache_endpoint, "", dependencies)

                                if dependencies is not None:
                                    repo.dependencies.extend(dependencies)
                            except Exception as e:
                                logger.warning(
                                    f"Failed to crawl dependencies for {repo_full_name}: {e}"
                                )

                        if self.crawl_dependents:
                            try:
                                cache_endpoint = f"dependents/{repo_full_name}"
                                cache_params = (
                                    f"min_stars={self.min_stars}&max_dependents={self.max_dependents}"
                                )
                                cached_dependents = (
                                    self.client.cache.get(cache_endpoint, cache_params)
                                    if self.client.cache
                                    else None
                                )

                                if cached_dependents is not None:
                                    dependents = cached_dependents
                                    logger.debug(f"Cache hit for dependents of {repo_full_name}")
                                else:
                                    dependents = fetch_dependents(
                                        repo_full_name,
                                        min_stars=self.min_stars,
                                        max_dependents=self.max_dependents,
                                    )
                                    if dependents is not None and self.client.cache:
                                        self.client.cache.set(
                                            cache_endpoint, cache_params, dependents
                                        )

                                if dependents is not None:
                                    repo.dependents.extend(dependents)
                                    for dep in dependents:
                                        items_to_queue.append(("repo", dep))
                            except Exception as e:
                                logger.warning(
                                    f"Failed to crawl dependents for {repo_full_name}: {e}"
                                )

                        with self.visited_lock:
                            for item_type, identifier in items_to_queue:
                                url = _kind_to_url(item_type, identifier)
                                if url not in self.visited:
                                    self.queue.append(
                                        (item_type, url, self.current_round + 1)
                                    )
                                    self._track_discovered_node(
                                        item_type,
                                        url,
                                        repo_url_value,
                                        "repo",
                                    )

                        return repo
                except Exception as exc:
                    logger.warning(
                        "Gimie hybrid failed for %s; falling back to GitHub API: %s",
                        repo_full_name,
                        exc,
                    )

            # ── Default GitHub API processing ──────────────────────────────
            repo_obj = self.client.get_repository(repo_url_value)
            if not repo_obj:
                return None
            
            is_cached = isinstance(repo_obj, dict)
            
            if is_cached:
                repo = RepoModel(
                    full_name=repo_obj['full_name'],
                    name=repo_obj.get('name', ''),
                    id=repo_obj.get('id', 0),
                    type=GitHubItemType.REPOSITORY,
                    owner=repo_obj.get('owner', ''),
                    is_fork=repo_obj.get('is_fork', False),
                    forked_from=repo_obj.get('parent'),
                    is_explored=True,
                    exploration_timestamp=datetime.now().isoformat(),
                )
                
                # Collect items to queue
                items_to_queue = []
                
                # Add owner
                owner_login = repo_obj.get('owner', '')
                owner_type_str = repo_obj.get('owner_type', 'User')
                if owner_login:
                    owner_type = 'org' if owner_type_str == 'Organization' else 'user'
                    items_to_queue.append((owner_type, owner_login))
                
                # Total contributor count (metadata) — captured in the cache
                # the first time the repo was fetched.
                cached_count = repo_obj.get('contributor_count')
                if isinstance(cached_count, int):
                    repo.contributor_count = cached_count

                # Record and queue every contributor. With an explicit
                # `max_contributors`, truncate to the top N (a `None` slice
                # bound keeps the whole list when there is no cap).
                cached_contributors = repo_obj.get('contributors', [])
                for contributor_login in cached_contributors[:self._contributor_limit()]:
                    repo.contributors.append(contributor_login)
                    items_to_queue.append(('user', contributor_login))

                # Cached issue/PR activity (populated by the GraphQL client when
                # crawl_issues/crawl_prs are set; REST cache omits these).
                repo.issue_authors.extend(repo_obj.get('issue_authors', []) or [])
                repo.pr_authors.extend(repo_obj.get('pr_authors', []) or [])
                repo.commenters.extend(repo_obj.get('commenters', []) or [])
                repo.pr_reviewers.extend(repo_obj.get('pr_reviewers', []) or [])

                # If it's a fork, add parent
                if repo.is_fork and repo.forked_from:
                    items_to_queue.append(('repo', repo.forked_from))
                
                # Crawl dependencies (downstream) - Cached path
                if self.crawl_dependencies:
                    try:
                        # Check cache
                        cache_endpoint = f"sbom/{repo_full_name}"
                        cached_deps = self.client.cache.get(cache_endpoint) if self.client.cache else None
                        
                        if cached_deps is not None:
                            dependencies = cached_deps
                            logger.debug(f"Cache hit for dependencies of {repo_full_name}")
                        else:
                            # Use current token for API calls
                            token = self.client.tokens[self.client.current_token_idx]
                            dependencies = fetch_dependencies_sbom(repo_full_name, token)
                            
                            if dependencies is not None:
                                # Save to cache
                                if self.client.cache:
                                    self.client.cache.set(cache_endpoint, "", dependencies)
                        
                        # Add dependencies to repo model (whether from cache or API)
                        if dependencies is not None:
                            repo.dependencies.extend(dependencies)
                            # Do not queue dependencies for next round
                            # for dep in dependencies:
                            #     items_to_queue.append(('repo', dep))
                    except Exception as e:
                        logger.warning(f"Failed to crawl dependencies for {repo_full_name}: {e}")

                # Crawl dependents (upstream) - Cached path
                if self.crawl_dependents:
                    try:
                        # Check cache
                        cache_endpoint = f"dependents/{repo_full_name}"
                        cache_params = f"min_stars={self.min_stars}&max_dependents={self.max_dependents}"
                        cached_dependents = self.client.cache.get(cache_endpoint, cache_params) if self.client.cache else None
                        
                        if cached_dependents is not None:
                            dependents = cached_dependents
                            logger.debug(f"Cache hit for dependents of {repo_full_name}")
                        else:
                            dependents = fetch_dependents(
                                repo_full_name, 
                                min_stars=self.min_stars,
                                max_dependents=self.max_dependents
                            )
                            
                            if dependents is not None:
                                # Save to cache
                                if self.client.cache:
                                    self.client.cache.set(cache_endpoint, cache_params, dependents)
                        
                        # Add dependents to repo model and queue (whether from cache or API)
                        if dependents is not None:
                            repo.dependents.extend(dependents)
                            for dep in dependents:
                                items_to_queue.append(('repo', dep))
                    except Exception as e:
                        logger.warning(f"Failed to crawl dependents for {repo_full_name}: {e}")

                # Add all items to queue in a single lock acquisition (URLs).
                with self.visited_lock:
                    for item_type, identifier in items_to_queue:
                        url = _kind_to_url(item_type, identifier)
                        if url not in self.visited:
                            self.queue.append((item_type, url, self.current_round + 1))
                            self._track_discovered_node(item_type, url, repo_url_value, 'repo')
            else:
                repo = RepoModel(
                    full_name=repo_obj.full_name,
                    name=repo_obj.name,
                    id=repo_obj.id,
                    type=GitHubItemType.REPOSITORY,
                    owner=repo_obj.owner.login,
                    is_fork=repo_obj.fork,
                    forked_from=repo_obj.parent.full_name if repo_obj.parent else None,
                    is_explored=True,
                    exploration_timestamp=datetime.now().isoformat(),
                )
                
                # Collect items to queue
                items_to_queue = []
                
                # Add owner
                owner_type = 'org' if repo_obj.owner.type == 'Organization' else 'user'
                items_to_queue.append((owner_type, repo_obj.owner.login))
                
                # Get contributors from the live API. ``totalCount`` is one
                # cheap ``per_page=1`` request kept as metadata. Every
                # contributor is recorded and queued; an explicit
                # `max_contributors` truncates to the top N (never skips).
                try:
                    contributors = self.client._make_request(repo_obj.get_contributors)
                    try:
                        repo.contributor_count = int(contributors.totalCount)
                    except Exception as e:
                        logger.debug(
                            f"Failed to read contributor totalCount for {repo_full_name}: {e}"
                        )

                    limit = self._contributor_limit()
                    for i, contributor in enumerate(contributors):
                        if limit is not None and i >= limit:
                            break
                        repo.contributors.append(contributor.login)
                        items_to_queue.append(('user', contributor.login))
                except Exception as e:
                    logger.warning(f"Failed to get contributors for repo {repo_full_name}: {e}")
                
                # If it's a fork, add parent
                if repo.is_fork and repo.forked_from:
                    items_to_queue.append(('repo', repo.forked_from))
                
                # Crawl dependencies (downstream)
                if self.crawl_dependencies:
                    try:
                        # Check cache
                        cache_endpoint = f"sbom/{repo_full_name}"
                        cached_deps = self.client.cache.get(cache_endpoint) if self.client.cache else None
                        
                        if cached_deps is not None:
                            dependencies = cached_deps
                            logger.debug(f"Cache hit for dependencies of {repo_full_name}")
                        else:
                            # Use current token for API calls
                            token = self.client.tokens[self.client.current_token_idx]
                            dependencies = fetch_dependencies_sbom(repo_full_name, token)
                            
                            if dependencies is not None:
                                # Save to cache
                                if self.client.cache:
                                    self.client.cache.set(cache_endpoint, "", dependencies)
                        
                        # Add dependencies to repo model (whether from cache or API)
                        if dependencies is not None:
                            repo.dependencies.extend(dependencies)
                            # Do not queue dependencies for next round
                            # for dep in dependencies:
                            #     items_to_queue.append(('repo', dep))
                    except Exception as e:
                        logger.warning(f"Failed to crawl dependencies for {repo_full_name}: {e}")

                # Crawl dependents (upstream)
                if self.crawl_dependents:
                    try:
                        # Check cache
                        cache_endpoint = f"dependents/{repo_full_name}"
                        cache_params = f"min_stars={self.min_stars}&max_dependents={self.max_dependents}"
                        cached_dependents = self.client.cache.get(cache_endpoint, cache_params) if self.client.cache else None
                        
                        if cached_dependents is not None:
                            dependents = cached_dependents
                            logger.debug(f"Cache hit for dependents of {repo_full_name}")
                        else:
                            dependents = fetch_dependents(
                                repo_full_name, 
                                min_stars=self.min_stars,
                                max_dependents=self.max_dependents
                            )
                            
                            if dependents is not None:
                                # Save to cache
                                if self.client.cache:
                                    self.client.cache.set(cache_endpoint, cache_params, dependents)
                        
                        # Add dependents to repo model and queue (whether from cache or API)
                        if dependents is not None:
                            repo.dependents.extend(dependents)
                            for dep in dependents:
                                items_to_queue.append(('repo', dep))
                    except Exception as e:
                        logger.warning(f"Failed to crawl dependents for {repo_full_name}: {e}")

                # Issue / PR activity (record only — does not queue new nodes).
                if self.crawl_issues:
                    self._fetch_repo_issues(repo_obj, repo)
                if self.crawl_prs:
                    self._fetch_repo_prs(repo_obj, repo)

                # Add all items to queue in a single lock acquisition (URLs).
                with self.visited_lock:
                    for item_type, identifier in items_to_queue:
                        url = _kind_to_url(item_type, identifier)
                        if url not in self.visited:
                            self.queue.append((item_type, url, self.current_round + 1))
                            self._track_discovered_node(item_type, url, repo_url_value, 'repo')

            return repo
        except Exception as e:
            logger.error(f"Error processing repository {repo_full_name}: {e}")
            return None

    def _fetch_repo_issues(self, repo_obj, repo: RepoModel):
        """Populate repo.issue_authors and repo.commenters from the issues API.

        Iterates up to self.issue_max true issues (excludes PRs via the
        `pull_request` attribute). Per issue, also fetches conversation
        comments and records commenter logins.
        """
        try:
            issues = self.client._make_request(repo_obj.get_issues, state="all")
        except Exception as e:
            logger.warning(f"Failed to list issues for {repo.full_name}: {e}")
            return
        if issues is None:
            return

        authors_seen: Set[str] = set()
        commenters_seen: Set[str] = set(repo.commenters)
        count = 0
        try:
            for issue in issues:
                if issue.pull_request is not None:
                    continue
                if count >= self.issue_max:
                    break
                count += 1

                author = getattr(issue, "user", None)
                if author is not None and author.login not in authors_seen:
                    authors_seen.add(author.login)
                    repo.issue_authors.append(author.login)

                try:
                    comments = self.client._make_request(issue.get_comments)
                    if comments is None:
                        continue
                    for c in comments:
                        cuser = getattr(c, "user", None)
                        if cuser is None:
                            continue
                        if cuser.login in commenters_seen:
                            continue
                        commenters_seen.add(cuser.login)
                        repo.commenters.append(cuser.login)
                except Exception as e:
                    logger.warning(f"Failed to get comments for issue in {repo.full_name}: {e}")
        except Exception as e:
            logger.warning(f"Issue iteration failed for {repo.full_name}: {e}")

    def _fetch_repo_prs(self, repo_obj, repo: RepoModel):
        """Populate repo.pr_authors, repo.pr_reviewers, repo.commenters from the PRs API.

        Iterates up to self.pr_max PRs. Per PR also records conversation
        comments (issue-comments) and review submitters.
        """
        try:
            pulls = self.client._make_request(repo_obj.get_pulls, state="all")
        except Exception as e:
            logger.warning(f"Failed to list PRs for {repo.full_name}: {e}")
            return
        if pulls is None:
            return

        authors_seen: Set[str] = set()
        reviewers_seen: Set[str] = set()
        commenters_seen: Set[str] = set(repo.commenters)
        count = 0
        try:
            for pr in pulls:
                if count >= self.pr_max:
                    break
                count += 1

                author = getattr(pr, "user", None)
                if author is not None and author.login not in authors_seen:
                    authors_seen.add(author.login)
                    repo.pr_authors.append(author.login)

                try:
                    reviews = self.client._make_request(pr.get_reviews)
                    if reviews is not None:
                        for r in reviews:
                            ruser = getattr(r, "user", None)
                            if ruser is None:
                                continue
                            if ruser.login in reviewers_seen:
                                continue
                            reviewers_seen.add(ruser.login)
                            repo.pr_reviewers.append(ruser.login)
                except Exception as e:
                    logger.warning(f"Failed to get reviews for PR in {repo.full_name}: {e}")

                try:
                    comments = self.client._make_request(pr.get_issue_comments)
                    if comments is not None:
                        for c in comments:
                            cuser = getattr(c, "user", None)
                            if cuser is None:
                                continue
                            if cuser.login in commenters_seen:
                                continue
                            commenters_seen.add(cuser.login)
                            repo.commenters.append(cuser.login)
                except Exception as e:
                    logger.warning(f"Failed to get PR comments in {repo.full_name}: {e}")
        except Exception as e:
            logger.warning(f"PR iteration failed for {repo.full_name}: {e}")
    
    def _process_node(self, node_type: str, identifier: str) -> Optional[tuple]:
        """
        Process a single node and return (type, entity) tuple.
        
        Args:
            node_type: Type of node ('user', 'org', 'repo', 'user_or_org')
            identifier: Node identifier (username, org name, or repo full name)
        
        Returns:
            Tuple of (entity_type, entity_object) or None if processing failed
        """
        try:
            if node_type == 'user_or_org':
                # Try as user first
                user = self._process_user(identifier)
                if user:
                    return ('user', user)
                else:
                    # Try as organization
                    org = self._process_organization(identifier)
                    if org:
                        return ('org', org)
            
            elif node_type == 'user':
                user = self._process_user(identifier)
                if user:
                    return ('user', user)
            
            elif node_type == 'org':
                org = self._process_organization(identifier)
                if org:
                    return ('org', org)
            
            elif node_type == 'repo':
                repo = self._process_repository(identifier)
                if repo:
                    return ('repo', repo)
            
            return None
        except Exception as e:
            logger.error(f"Error processing node {node_type}:{identifier}: {e}")
            return None
    
    def crawl(self, show_progress: bool = True):
        """Execute the BFS crawl with progress tracking.
        
        Args:
            show_progress: Whether to show progress bars (default: True)
        """
        start_time = datetime.now()
        start_time_str = start_time.strftime("%Y-%m-%d %H:%M:%S")
        logger.info(f"Starting crawl at {start_time_str} for {self.max_rounds} rounds")
        if show_progress:
            print(f"\n🚀 Crawl started at {start_time_str}")
            print(f"📊 Target: {self.max_rounds} rounds\n")
        
        # Create overall progress bar for rounds
        rounds_pbar = tqdm(
            total=self.max_rounds,
            desc="Overall Progress",
            unit="round",
            position=0,
            disable=not show_progress,
            initial=self.current_round
        )
        
        try:
            while self.queue and self.current_round < self.max_rounds:
                # Honour cooperative cancel between rounds. Inside a single
                # round we let the in-flight thread pool drain (work that's
                # already mid-network-call finishes) and then break.
                if self.cancel_requested:
                    logger.info("Crawl cancellation requested — exiting BFS")
                    break

                # If paused, sleep in 1s ticks until either the flag clears
                # or a cancel comes in. Pause is a between-rounds construct
                # — same reason as cancel: don't tear down active work.
                while self.pause_requested and not self.cancel_requested:
                    import time as _t
                    _t.sleep(1.0)
                if self.cancel_requested:
                    logger.info("Crawl cancellation while paused — exiting BFS")
                    break

                # Start new round
                round_start_time = __import__('time').time()
                nodes_in_round = []
                
                # Count nodes to process in this round
                nodes_this_round = sum(1 for _, _, node_round in self.queue if node_round == self.current_round)
                
                # Get current time for round
                round_time_str = datetime.now().strftime("%H:%M:%S")
                
                # Create progress bar for current round
                round_pbar = tqdm(
                    total=nodes_this_round,
                    desc=f"Round {self.current_round} [{round_time_str}]",
                    unit="node",
                    position=1,
                    leave=False,
                    disable=not show_progress
                )
                
                # Collect nodes to process in this round
                nodes_to_process = []
                while self.queue:
                    node_type, identifier, node_round = self.queue[0]
                    
                    if node_round > self.current_round:
                        # Reached next round
                        break
                    
                    self.queue.popleft()
                    
                    # Skip if already visited
                    if identifier in self.visited:
                        continue
                    
                    self.visited.add(identifier)
                    nodes_to_process.append((node_type, identifier))
                
                # Process nodes concurrently in batches
                with ThreadPoolExecutor(max_workers=self.batch_size) as executor:
                    # Submit all tasks
                    future_to_node = {
                        executor.submit(self._process_node, node_type, identifier): (node_type, identifier)
                        for node_type, identifier in nodes_to_process
                    }
                    
                    # Process results as they complete
                    for future in as_completed(future_to_node):
                        node_type, identifier = future_to_node[future]
                        nodes_in_round.append((node_type, identifier))
                        
                        try:
                            result = future.result()
                            if result:
                                entity_type, entity = result
                                
                                # Add to graph (thread-safe)
                                with self.graph_lock:
                                    if entity_type == 'user':
                                        self.graph.add_user(entity)
                                    elif entity_type == 'org':
                                        self.graph.add_org(entity)
                                    elif entity_type == 'repo':
                                        self.graph.add_repo(entity)
                        except Exception as e:
                            logger.error(f"Error processing {node_type}:{identifier}: {e}")
                        
                        # Update progress
                        round_pbar.update(1)
                
                round_pbar.close()
            
                # Round statistics - count actual entities found, not queued types
                round_time = __import__('time').time() - round_start_time
                
                # Count entities PROCESSED (visited) in this round by type
                # Look at what was actually added to the graph (check if visited)
                users_this_round = sum(1 for t, id in nodes_in_round if id in self.graph.users and id in self.visited)
                orgs_this_round = sum(1 for t, id in nodes_in_round if id in self.graph.orgs and id in self.visited)
                repos_this_round = sum(1 for t, id in nodes_in_round if id in self.graph.repos and id in self.visited)
                
                # Count items in queue by type
                queued_users = sum(1 for t, _, _ in self.queue if t in ['user', 'user_or_org'])
                queued_orgs = sum(1 for t, _, _ in self.queue if t == 'org')
                queued_repos = sum(1 for t, _, _ in self.queue if t == 'repo')
                
                round_stat = {
                    'round': self.current_round,
                    'nodes_processed': len(nodes_in_round),
                    'users_found': users_this_round,
                    'orgs_found': orgs_this_round,
                    'repos_found': repos_this_round,
                    'time_seconds': round_time,
                    'queue_size': len(self.queue),
                    'queued_users': queued_users,
                    'queued_orgs': queued_orgs,
                    'queued_repos': queued_repos,
                }
                self.round_stats.append(round_stat)
                
                # Update overall progress bar with statistics
                rounds_pbar.set_postfix({
                    'nodes': len(nodes_in_round),
                    'users': users_this_round,
                    'orgs': orgs_this_round,
                    'repos': repos_this_round,
                    'queue': f"{len(self.queue)} ({queued_users}u/{queued_orgs}o/{queued_repos}r)"
                })
                rounds_pbar.update(1)
                
                logger.info(f"Round {self.current_round} completed: "
                           f"{round_stat['nodes_processed']} nodes processed in {round_time:.1f}s, "
                           f"{round_stat['queue_size']} nodes in queue "
                           f"({queued_users} users, {queued_orgs} orgs, {queued_repos} repos)")
                
                self.current_round += 1
                
                # Save state after each round
                if self.state_file:
                    self.save_state()
                
                # Call incremental export callback if registered
                if self.incremental_export_callback:
                    self.incremental_export_callback(self.current_round - 1)
        finally:
            rounds_pbar.close()
        
        end_time = datetime.now()
        end_time_str = end_time.strftime("%Y-%m-%d %H:%M:%S")
        duration = end_time - start_time
        
        # Format duration in human-readable format
        total_seconds = int(duration.total_seconds())
        hours = total_seconds // 3600
        minutes = (total_seconds % 3600) // 60
        seconds = total_seconds % 60
        
        if hours > 0:
            duration_str = f"{hours}h {minutes}m {seconds}s"
        elif minutes > 0:
            duration_str = f"{minutes}m {seconds}s"
        else:
            duration_str = f"{seconds}s"
        
        logger.info(f"Crawl completed after {self.current_round} rounds")
        logger.info(f"Ended at {end_time_str} (Duration: {duration_str})")
        logger.info(f"Total nodes: {len(self.visited)}")
        logger.info(f"Users: {len(self.graph.users)}, Orgs: {len(self.graph.orgs)}, "
                   f"Repos: {len(self.graph.repos)}")
        
        if show_progress:
            print(f"\n✅ Crawl completed at {end_time_str}")
            print(f"⏱️  Total duration: {duration_str}")
            print(f"📦 Collected: {len(self.graph.users)} users, {len(self.graph.orgs)} orgs, {len(self.graph.repos)} repos\n")
    
    def get_statistics(self) -> Dict:
        """Get crawler statistics."""
        return {
            'rounds_completed': self.current_round,
            'total_nodes': len(self.visited),
            'users': len(self.graph.users),
            'organizations': len(self.graph.orgs),
            'repositories': len(self.graph.repos),
            'round_stats': self.round_stats,
            'api_stats': self.client.get_stats(),
        }
    
    def export_round(
        self,
        output_dir: Path,
        round_num: int,
        visualize: bool = False,
        visualize_clusters: bool = False,
        show_unexplored: bool = False,
        no_json: bool = False,
        no_csv: bool = False
    ) -> Path:
        """
        Export current graph state for a specific round.
        
        Args:
            output_dir: Base directory for output files
            round_num: Current round number
            visualize: Generate main visualization
            visualize_clusters: Generate cluster visualizations
            show_unexplored: Include unexplored (discovered) nodes in visualizations
            no_json: Skip JSON export
            no_csv: Skip CSV export
            
        Returns:
            Path to the round-specific output directory
        """
        from .io_utils import export_to_json, export_to_csv, export_nodes_csv
        from .visualization import visualize_graph, visualize_clusters as viz_clusters, VISUALIZATION_AVAILABLE
        
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        round_dir = output_dir / f"{timestamp}.round_{round_num:02d}"
        round_dir.mkdir(parents=True, exist_ok=True)
        
        logger.info(f"Exporting round {round_num} to {round_dir}")
        
        # Export JSON
        if not no_json:
            json_path = round_dir / f"{timestamp}.graph.json"
            export_to_json(self.graph, json_path)
            logger.debug(f"JSON exported to {json_path}")
        
        # Export CSVs
        if not no_csv:
            edges_csv = round_dir / f"{timestamp}.edges.csv"
            export_to_csv(self.graph, edges_csv, self.seed_nodes)
            logger.debug(f"Edges CSV exported to {edges_csv}")
            
            nodes_csv = round_dir / f"{timestamp}.nodes.csv"
            export_nodes_csv(
                self.graph, 
                nodes_csv, 
                self.seed_nodes,
                discovered_nodes=self.discovered_nodes,
            )
            logger.debug(f"Nodes CSV exported to {nodes_csv}")
        
        # Optional visualizations
        if visualize or visualize_clusters:
            if not VISUALIZATION_AVAILABLE:
                logger.warning("Visualization skipped: networkx/matplotlib not installed")
            else:
                # Only include discovered nodes if requested
                discovered = self.discovered_nodes if show_unexplored else None
                
                if visualize:
                    viz_path = round_dir / f"{timestamp}.graph.png"
                    try:
                        visualize_graph(self.graph, viz_path, self.seed_nodes, self.visited, discovered)
                        logger.debug(f"Visualization exported to {viz_path}")
                    except Exception as e:
                        logger.error(f"Visualization failed: {e}")
                
                if visualize_clusters:
                    clusters_dir = round_dir / f"{timestamp}.clusters"
                    try:
                        viz_clusters(self.graph, clusters_dir, self.seed_nodes, self.visited, discovered)
                        logger.debug(f"Cluster visualizations exported to {clusters_dir}/")
                    except Exception as e:
                        logger.error(f"Cluster visualization failed: {e}")
        
        logger.info(f"Round {round_num} export completed")
        return round_dir
