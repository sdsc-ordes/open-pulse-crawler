"""Pydantic models for GitHub entities."""

from typing import List, Dict, Optional
from pydantic import BaseModel, Field
from enum import Enum


class GitHubItemType(str, Enum):
    """Types of GitHub entities."""
    USER = "User"
    ORGANIZATION = "Organization"
    REPOSITORY = "Repository"
    TEAM = "Team"
    BOT = "Bot"
    UNKNOWN = "Unknown"


class BaseEntityModel(BaseModel):
    """Base model for all GitHub entities."""
    is_explored: bool = False
    exploration_timestamp: Optional[str] = None


class UserModel(BaseEntityModel):
    """Model representing a GitHub user."""
    login: str
    name: str = ""
    id: int = 0
    type: GitHubItemType = GitHubItemType.USER

    # Repos the user owns
    authored_repositories: List[str] = Field(default_factory=list)
    # Repos the user has forked
    forked_repositories: List[str] = Field(default_factory=list)

    # Users who follow this user
    followers: List[str] = Field(default_factory=list)
    # Users this user follows
    following: List[str] = Field(default_factory=list)

    # Repos the user has starred
    starred_repositories: List[str] = Field(default_factory=list)
    # Repos the user is subscribed to (watching for notifications)
    watched_repositories: List[str] = Field(default_factory=list)


class OrgModel(BaseEntityModel):
    """Model representing a GitHub organization."""
    login: str
    name: str = ""
    id: int = 0
    type: GitHubItemType = GitHubItemType.ORGANIZATION
    members: List[str] = Field(default_factory=list)

    # Org-owned repos that are original
    authored_repositories: List[str] = Field(default_factory=list)
    # Org-owned repos that are forks
    forked_repositories: List[str] = Field(default_factory=list)


class RepoModel(BaseEntityModel):
    """Model representing a GitHub repository."""
    full_name: str
    name: str = ""
    id: int = 0
    type: GitHubItemType = GitHubItemType.REPOSITORY
    contributors: List[str] = Field(default_factory=list)
    owner: str = ""

    # Fork information
    is_fork: bool = False
    forked_from: Optional[str] = None

    # Dependency information
    dependents: List[str] = Field(default_factory=list)
    dependencies: List[str] = Field(default_factory=list)

    # Issue and PR activity (opt-in via --crawl-issues / --crawl-prs)
    issue_authors: List[str] = Field(default_factory=list)
    pr_authors: List[str] = Field(default_factory=list)
    # Conversation commenters across issues and PRs (both fetched via the issues API).
    commenters: List[str] = Field(default_factory=list)
    # Formal review submitters on PRs.
    pr_reviewers: List[str] = Field(default_factory=list)

    # Total contributor count reported by GitHub (metadata). Captured the
    # first time the repo is fetched and persisted in the cache. ``None``
    # means we don't have a count yet. `contributors` above holds them all
    # by default; it is shorter than this only when `max_contributors` caps it.
    contributor_count: Optional[int] = None


class TeamModel(BaseEntityModel):
    """Model representing a GitHub organization team."""
    # full_name is "org_login/team_slug"
    full_name: str
    slug: str
    name: str = ""
    id: int = 0
    type: GitHubItemType = GitHubItemType.TEAM
    org: str = ""
    description: str = ""
    privacy: str = ""

    # Parent team's full_name when this is a child team
    parent: Optional[str] = None

    members: List[str] = Field(default_factory=list)
    repositories: List[str] = Field(default_factory=list)


class GraphData(BaseModel):
    """Holds references to users, orgs, repos, and teams discovered."""
    users: Dict[str, UserModel] = Field(default_factory=dict)
    orgs: Dict[str, OrgModel] = Field(default_factory=dict)
    repos: Dict[str, RepoModel] = Field(default_factory=dict)
    teams: Dict[str, TeamModel] = Field(default_factory=dict)

    def add_user(self, user: UserModel):
        """Add a user to the graph."""
        self.users[user.login] = user

    def add_org(self, org: OrgModel):
        """Add an organization to the graph."""
        self.orgs[org.login] = org

    def add_repo(self, repo: RepoModel):
        """Add a repository to the graph."""
        self.repos[repo.full_name] = repo

    def add_team(self, team: TeamModel):
        """Add a team to the graph."""
        self.teams[team.full_name] = team

    def has_user(self, login: str) -> bool:
        """Check if user exists in graph."""
        return login in self.users

    def has_org(self, login: str) -> bool:
        """Check if org exists in graph."""
        return login in self.orgs

    def has_repo(self, full_name: str) -> bool:
        """Check if repo exists in graph."""
        return full_name in self.repos

    def has_team(self, full_name: str) -> bool:
        """Check if team exists in graph."""
        return full_name in self.teams

    def get_user(self, login: str) -> Optional[UserModel]:
        """Get a user by login."""
        return self.users.get(login)

    def get_org(self, login: str) -> Optional[OrgModel]:
        """Get an org by login."""
        return self.orgs.get(login)

    def get_repo(self, full_name: str) -> Optional[RepoModel]:
        """Get a repo by full name."""
        return self.repos.get(full_name)

    def get_team(self, full_name: str) -> Optional[TeamModel]:
        """Get a team by full name (org/slug)."""
        return self.teams.get(full_name)
