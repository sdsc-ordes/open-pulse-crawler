"""Pydantic models for graph entities.

Nodes (user, org, repo, team) are keyed by their canonical public URL
in :class:`GraphData` — see :mod:`open_pulse_crawler.node_id`. Each
model keeps the platform-native shorthand (``login`` for user/org,
``full_name`` for repo/team) alongside the canonical ``url`` so display
layers, API callers, and the crawler can still pull the bare identifier
when they need it.

Edge-list fields (``followers``, ``contributors``, ``dependencies``,
``members``, ...) keep storing the bare platform shorthand (logins or
``owner/repo`` strings) rather than URLs — this is a pragmatic choice
to keep the internal representation compact. The boundary that produces
the public CSV / JSON-LD output converts these to URLs at write time so
consumers join on a uniform key.
"""

from __future__ import annotations

from enum import Enum
from typing import Annotated, Any, Dict, List, Literal, Optional, Union

from pydantic import BaseModel, Field, model_validator

from .node_id import (
    extract_full_name,
    extract_login,
    extract_team_parts,
    repo_url as _repo_url,
    team_url as _team_url,
    user_url as _user_url,
)


class GitHubItemType(str, Enum):
    """Types of GitHub entities."""

    USER = "User"
    ORGANIZATION = "Organization"
    REPOSITORY = "Repository"
    TEAM = "Team"
    BOT = "Bot"
    UNKNOWN = "Unknown"


class ExternalIdentifier(BaseModel):
    """A typed external identifier (e.g. ORCID, ROR) attached to a node."""

    scheme: str
    value: str


class BaseEntityModel(BaseModel):
    """Base model for all graph entities."""

    is_explored: bool = False
    exploration_timestamp: Optional[str] = None
    # Canonical platform identifier. Defaults to "github" until the
    # GitLab adapter lands; per-host dispatch flips this on construction.
    platform: str = "github"
    # Typed cross-references to other identifier systems (ORCID, ROR, ...).
    external_identifiers: List[ExternalIdentifier] = Field(default_factory=list)
    # Free-form bag for platform-specific or experimental fields that don't
    # warrant a first-class column yet.
    extras: Dict[str, Any] = Field(default_factory=dict)


class UserModel(BaseEntityModel):
    """A user on the source platform."""

    subkind: Literal["GitHubUser"] = "GitHubUser"
    # Canonical URL — primary key in GraphData.users.
    url: str
    # Platform-native login, kept alongside the URL for display + API calls.
    login: str
    name: str = ""
    id: int = 0
    type: GitHubItemType = GitHubItemType.USER

    # Repos the user owns (full_name strings).
    authored_repositories: List[str] = Field(default_factory=list)
    # Repos the user has forked (full_name strings).
    forked_repositories: List[str] = Field(default_factory=list)

    # Users who follow this user (login strings).
    followers: List[str] = Field(default_factory=list)
    # Users this user follows (login strings).
    following: List[str] = Field(default_factory=list)

    # Repos the user has starred (full_name strings).
    starred_repositories: List[str] = Field(default_factory=list)
    # Repos the user is subscribed to (full_name strings).
    watched_repositories: List[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _fill_url_and_login(cls, data):
        """Allow callers to provide login XOR url; fill the other in.

        Constructing a UserModel with only ``login`` (the legacy shape) is
        still supported — ``url`` is derived. Constructing with only
        ``url`` derives the login by parsing the URL. If both are given
        we trust the caller and do not cross-check.
        """
        if not isinstance(data, dict):
            return data
        has_url = bool(data.get("url"))
        has_login = bool(data.get("login"))
        if has_url and not has_login:
            data["login"] = extract_login(data["url"])
        elif has_login and not has_url:
            data["url"] = _user_url(data["login"])
        return data


class OrgModel(BaseEntityModel):
    """An organization on the source platform."""

    subkind: Literal["GitHubOrganization"] = "GitHubOrganization"
    url: str
    login: str
    name: str = ""
    id: int = 0
    type: GitHubItemType = GitHubItemType.ORGANIZATION
    members: List[str] = Field(default_factory=list)  # login strings

    # Org-owned repos that are original (full_name strings).
    authored_repositories: List[str] = Field(default_factory=list)
    # Org-owned repos that are forks (full_name strings).
    forked_repositories: List[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _fill_url_and_login(cls, data):
        if not isinstance(data, dict):
            return data
        has_url = bool(data.get("url"))
        has_login = bool(data.get("login"))
        if has_url and not has_login:
            data["login"] = extract_login(data["url"])
        elif has_login and not has_url:
            data["url"] = _user_url(data["login"])
        return data


class RepoModel(BaseEntityModel):
    """A repository on the source platform."""

    subkind: Literal["GitHubRepository"] = "GitHubRepository"
    url: str
    full_name: str
    name: str = ""
    id: int = 0
    type: GitHubItemType = GitHubItemType.REPOSITORY
    contributors: List[str] = Field(default_factory=list)  # login strings
    # Owner login (kept for display + API calls). The owner's URL is the
    # parent of ``self.url``; callers can build it via node_id.user_url
    # if they need it.
    owner: str = ""

    # Fork information
    is_fork: bool = False
    # ``owner/repo`` of the upstream repo this is a fork of; None for non-forks.
    forked_from: Optional[str] = None

    # Dependency information (full_name strings).
    dependents: List[str] = Field(default_factory=list)
    dependencies: List[str] = Field(default_factory=list)

    # Issue / PR activity edges (login strings).
    issue_authors: List[str] = Field(default_factory=list)
    pr_authors: List[str] = Field(default_factory=list)
    # Conversation commenters across issues and PRs (both fetched via the issues API).
    commenters: List[str] = Field(default_factory=list)
    # Formal review submitters on PRs.
    pr_reviewers: List[str] = Field(default_factory=list)

    # Total contributor count reported by GitHub (metadata). Captured the
    # first time the repo is fetched and persisted in the cache. ``None``
    # means we don't have a count yet. ``contributors`` above holds them
    # all by default; it is shorter than this only when ``max_contributors``
    # caps it.
    contributor_count: Optional[int] = None

    @model_validator(mode="before")
    @classmethod
    def _fill_url_and_full_name(cls, data):
        if not isinstance(data, dict):
            return data
        has_url = bool(data.get("url"))
        has_full = bool(data.get("full_name"))
        if has_url and not has_full:
            data["full_name"] = extract_full_name(data["url"])
        elif has_full and not has_url:
            data["url"] = _repo_url(data["full_name"])
        # Owner is convenient and cheap to derive from full_name.
        if not data.get("owner") and data.get("full_name"):
            data["owner"] = data["full_name"].split("/", 1)[0]
        return data


class TeamModel(BaseEntityModel):
    """A team within an organization on the source platform."""

    subkind: Literal["GitHubTeam"] = "GitHubTeam"
    url: str
    # full_name is "org_login/team_slug" — kept for display / log lines.
    full_name: str
    slug: str = ""
    name: str = ""
    id: int = 0
    type: GitHubItemType = GitHubItemType.TEAM
    # Org login (kept for display). The org's URL can be built via
    # node_id.user_url(self.org) when needed.
    org: str = ""
    description: str = ""
    privacy: str = ""

    # ``org/slug`` of the parent team when this is a child team.
    parent: Optional[str] = None

    members: List[str] = Field(default_factory=list)  # login strings
    repositories: List[str] = Field(default_factory=list)  # full_name strings

    @model_validator(mode="before")
    @classmethod
    def _fill_url_and_full_name(cls, data):
        if not isinstance(data, dict):
            return data
        has_url = bool(data.get("url"))
        has_full = bool(data.get("full_name"))
        has_slug = bool(data.get("slug"))
        has_org = bool(data.get("org"))
        if has_url and not has_full:
            org, slug = extract_team_parts(data["url"])
            data["full_name"] = f"{org}/{slug}"
            data.setdefault("org", org)
            data.setdefault("slug", slug)
        elif has_full and not has_url:
            org, slug = data["full_name"].split("/", 1)
            data["url"] = _team_url(org, slug)
            data.setdefault("org", org)
            data.setdefault("slug", slug)
        elif has_org and has_slug and not has_url:
            data["url"] = _team_url(data["org"], data["slug"])
            data["full_name"] = f"{data['org']}/{data['slug']}"
        return data


# --- GitLab subclasses ------------------------------------------------------
#
# These inherit every GitHub-shaped field and override ``subkind`` so the
# Pydantic v2 discriminated union below can pick the right concrete class
# when deserializing a mixed-platform GraphData snapshot.


class GitLabUserModel(UserModel):
    """A user on a GitLab instance."""

    subkind: Literal["GitLabUser"] = "GitLabUser"
    state: Literal["active", "blocked", "deactivated"] = "active"
    public_email: str = ""


class GitLabGroupModel(OrgModel):
    """A group on a GitLab instance (the GitLab analogue of a GitHub org)."""

    subkind: Literal["GitLabGroup"] = "GitLabGroup"
    # URL of parent group when this is a subgroup; None for top-level groups.
    parent: Optional[str] = None
    visibility: Literal["private", "internal", "public"] = "public"


class GitLabProjectModel(RepoModel):
    """A project on a GitLab instance (the GitLab analogue of a GitHub repo)."""

    subkind: Literal["GitLabProject"] = "GitLabProject"
    visibility: Literal["private", "internal", "public"] = "public"
    # Owner namespace URL (a user or group); None when not yet resolved.
    namespace: Optional[str] = None


# Schema version bumped when the graph contract changed. v2 added URL-keyed
# nodes; v3 adds the ``subkind`` discriminator + ``extras`` /
# ``external_identifiers`` slots so GitLab subclasses can coexist with the
# GitHub-shaped concrete models in the same dicts.
GRAPH_SCHEMA_VERSION = 3


# Discriminated unions on ``subkind`` let the same dict hold either the
# GitHub concrete class or its GitLab counterpart. Pydantic v2 picks the
# right class on deserialization by reading the literal subkind tag.
UserNode = Annotated[Union[UserModel, GitLabUserModel], Field(discriminator="subkind")]
OrgNode = Annotated[Union[OrgModel, GitLabGroupModel], Field(discriminator="subkind")]
RepoNode = Annotated[Union[RepoModel, GitLabProjectModel], Field(discriminator="subkind")]


class GraphData(BaseModel):
    """Holds references to users, orgs, repos, and teams discovered.

    All dict keys are canonical URLs (see :mod:`open_pulse_crawler.node_id`).
    """

    schema_version: int = GRAPH_SCHEMA_VERSION
    users: Dict[str, UserNode] = Field(default_factory=dict)
    orgs: Dict[str, OrgNode] = Field(default_factory=dict)
    repos: Dict[str, RepoNode] = Field(default_factory=dict)
    teams: Dict[str, TeamModel] = Field(default_factory=dict)

    # --- mutation helpers ----------------------------------------------------

    def add_user(self, user: UserModel) -> None:
        """Add a user to the graph (keyed by its URL)."""
        self.users[user.url] = user

    def add_org(self, org: OrgModel) -> None:
        """Add an organization to the graph (keyed by its URL)."""
        self.orgs[org.url] = org

    def add_repo(self, repo: RepoModel) -> None:
        """Add a repository to the graph (keyed by its URL)."""
        self.repos[repo.url] = repo

    def add_team(self, team: TeamModel) -> None:
        """Add a team to the graph (keyed by its URL)."""
        self.teams[team.url] = team

    # --- membership tests ----------------------------------------------------

    def has_user(self, url: str) -> bool:
        return url in self.users

    def has_org(self, url: str) -> bool:
        return url in self.orgs

    def has_repo(self, url: str) -> bool:
        return url in self.repos

    def has_team(self, url: str) -> bool:
        return url in self.teams

    # --- lookups -------------------------------------------------------------

    def get_user(self, url: str) -> Optional[UserModel]:
        return self.users.get(url)

    def get_org(self, url: str) -> Optional[OrgModel]:
        return self.orgs.get(url)

    def get_repo(self, url: str) -> Optional[RepoModel]:
        return self.repos.get(url)

    def get_team(self, url: str) -> Optional[TeamModel]:
        return self.teams.get(url)

    # --- multi-platform convenience accessors --------------------------------

    def of_subkind(self, subkind: str):
        """Yield every node across all dicts whose ``subkind`` matches."""
        for d in (self.users, self.orgs, self.repos, self.teams):
            for n in d.values():
                if n.subkind == subkind:
                    yield n

    def by_platform(self, platform: str):
        """Yield every node across all dicts on the given platform."""
        for d in (self.users, self.orgs, self.repos, self.teams):
            for n in d.values():
                if n.platform == platform:
                    yield n

    def by_instance(self, instance_host: str):
        """Yield every node whose URL host matches ``instance_host``.

        ``instance`` is not yet a first-class field on Node — defer that to
        the task that introduces multi-instance routing. For now we derive
        the host from the canonical URL.
        """
        from urllib.parse import urlparse

        for d in (self.users, self.orgs, self.repos, self.teams):
            for n in d.values():
                if urlparse(n.url).netloc.lower() == instance_host:
                    yield n
