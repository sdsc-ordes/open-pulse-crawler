"""Pydantic models for graph entities.

Nodes (user, org, repo, team) are keyed by their canonical public URL
across the codebase — see :mod:`open_pulse_crawler.node_id`. Each model
keeps the platform-native shorthand (``login`` for user/org,
``full_name`` for repo/team) alongside the canonical ``url`` so that
display layers, API callers, and the crawler can still pull the bare
identifier when needed.

Edge fields (followers, contributors, dependencies, members, ...) hold
URL strings, not bare logins, so a graph that mixes platforms can be
keyed and indexed uniformly.
"""

from __future__ import annotations

from enum import Enum
from typing import Dict, List, Optional

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


class BaseEntityModel(BaseModel):
    """Base model for all graph entities."""

    is_explored: bool = False
    exploration_timestamp: Optional[str] = None
    # Canonical platform identifier. Defaults to "github" until the
    # GitLab adapter lands; per-host dispatch flips this on construction.
    platform: str = "github"


class UserModel(BaseEntityModel):
    """A user on the source platform."""

    # Canonical URL — primary key in GraphData.users.
    url: str
    # Platform-native login, kept alongside the URL for display + API calls.
    login: str
    name: str = ""
    id: int = 0
    type: GitHubItemType = GitHubItemType.USER

    # Repos the user owns (URLs).
    authored_repositories: List[str] = Field(default_factory=list)
    # Repos the user has forked (URLs).
    forked_repositories: List[str] = Field(default_factory=list)

    # Users who follow this user (URLs).
    followers: List[str] = Field(default_factory=list)
    # Users this user follows (URLs).
    following: List[str] = Field(default_factory=list)

    # Repos the user has starred (URLs).
    starred_repositories: List[str] = Field(default_factory=list)
    # Repos the user is subscribed to (URLs).
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

    url: str
    login: str
    name: str = ""
    id: int = 0
    type: GitHubItemType = GitHubItemType.ORGANIZATION
    members: List[str] = Field(default_factory=list)  # user URLs

    # Org-owned repos that are original (URLs).
    authored_repositories: List[str] = Field(default_factory=list)
    # Org-owned repos that are forks (URLs).
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

    url: str
    full_name: str
    name: str = ""
    id: int = 0
    type: GitHubItemType = GitHubItemType.REPOSITORY
    contributors: List[str] = Field(default_factory=list)  # user URLs
    # Owner login (kept for display + API calls). The owner's URL is the
    # parent of ``self.url``; callers can build it via node_id.user_url
    # if they need it.
    owner: str = ""

    # Fork information
    is_fork: bool = False
    # URL of the upstream repo this is a fork of; None for non-forks.
    forked_from: Optional[str] = None

    # Dependency information (URLs).
    dependents: List[str] = Field(default_factory=list)
    dependencies: List[str] = Field(default_factory=list)

    # Issue / PR activity edges (user URLs).
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

    # URL of the parent team when this is a child team.
    parent: Optional[str] = None

    members: List[str] = Field(default_factory=list)  # user URLs
    repositories: List[str] = Field(default_factory=list)  # repo URLs

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


# Schema version bumped when the graph contract changed to URL-keyed
# nodes. Snapshots written under earlier versions are not readable.
GRAPH_SCHEMA_VERSION = 2


class GraphData(BaseModel):
    """Holds references to users, orgs, repos, and teams discovered.

    All dict keys are canonical URLs (see :mod:`open_pulse_crawler.node_id`).
    """

    schema_version: int = GRAPH_SCHEMA_VERSION
    users: Dict[str, UserModel] = Field(default_factory=dict)
    orgs: Dict[str, OrgModel] = Field(default_factory=dict)
    repos: Dict[str, RepoModel] = Field(default_factory=dict)
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
