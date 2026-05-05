"""Pydantic models for GitHub entities."""

from typing import List, Dict, Optional
from pydantic import BaseModel, Field
from enum import Enum


class GitHubItemType(str, Enum):
    """Types of GitHub entities."""
    USER = "User"
    ORGANIZATION = "Organization"
    REPOSITORY = "Repository"
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

    # Total contributor count reported by GitHub. Captured once when the repo
    # is first fetched and persisted in the cache so subsequent crawls can
    # apply a `--max-contributors` skip rule without re-querying. ``None``
    # means we don't have a count yet (cache miss + count fetch failed).
    contributor_count: Optional[int] = None
    # True when the crawler skipped this repo's contributor expansion because
    # ``contributor_count`` exceeded the configured threshold. The repo node
    # itself remains in the graph (with owner / fork / deps as usual); only
    # contributor edges are dropped.
    skipped_high_contributors: bool = False


class GraphData(BaseModel):
    """Holds references to users, orgs, and repos discovered."""
    users: Dict[str, UserModel] = Field(default_factory=dict)
    orgs: Dict[str, OrgModel] = Field(default_factory=dict)
    repos: Dict[str, RepoModel] = Field(default_factory=dict)

    def add_user(self, user: UserModel):
        """Add a user to the graph."""
        self.users[user.login] = user

    def add_org(self, org: OrgModel):
        """Add an organization to the graph."""
        self.orgs[org.login] = org

    def add_repo(self, repo: RepoModel):
        """Add a repository to the graph."""
        self.repos[repo.full_name] = repo

    def has_user(self, login: str) -> bool:
        """Check if user exists in graph."""
        return login in self.users

    def has_org(self, login: str) -> bool:
        """Check if org exists in graph."""
        return login in self.orgs

    def has_repo(self, full_name: str) -> bool:
        """Check if repo exists in graph."""
        return full_name in self.repos

    def get_user(self, login: str) -> Optional[UserModel]:
        """Get a user by login."""
        return self.users.get(login)

    def get_org(self, login: str) -> Optional[OrgModel]:
        """Get an org by login."""
        return self.orgs.get(login)

    def get_repo(self, full_name: str) -> Optional[RepoModel]:
        """Get a repo by full name."""
        return self.repos.get(full_name)
