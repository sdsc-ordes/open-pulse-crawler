"""Basic tests for the crawler."""

import pytest
from open_pulse_crawler.models import (
    GitHubItemType, UserModel, OrgModel, RepoModel, TeamModel, GraphData
)


def test_user_model():
    """Test UserModel creation."""
    user = UserModel(
        login="testuser",
        name="Test User",
        id=123,
        type=GitHubItemType.USER
    )
    assert user.login == "testuser"
    assert user.name == "Test User"
    assert len(user.authored_repositories) == 0
    assert user.followers == []
    assert user.following == []


def test_user_model_follow_lists():
    """Test that follow lists round-trip through UserModel."""
    user = UserModel(
        login="alice",
        id=1,
        followers=["bob", "carol"],
        following=["bob"],
    )
    assert user.followers == ["bob", "carol"]
    assert user.following == ["bob"]


def test_user_model_star_watch_lists():
    """Test that starred/watched lists round-trip through UserModel."""
    user = UserModel(
        login="alice",
        id=1,
        starred_repositories=["org/a", "org/b"],
        watched_repositories=["org/a"],
    )
    assert user.starred_repositories == ["org/a", "org/b"]
    assert user.watched_repositories == ["org/a"]


def test_repo_model_issue_pr_fields():
    """Test issue/PR activity fields on RepoModel."""
    repo = RepoModel(
        full_name="org/repo",
        id=1,
        owner="org",
        issue_authors=["alice"],
        pr_authors=["bob"],
        commenters=["alice", "carol"],
        pr_reviewers=["dan"],
    )
    assert repo.issue_authors == ["alice"]
    assert repo.pr_authors == ["bob"]
    assert repo.commenters == ["alice", "carol"]
    assert repo.pr_reviewers == ["dan"]


def test_team_model_and_graph():
    """Test TeamModel and GraphData team operations."""
    team = TeamModel(
        full_name="acme/core",
        slug="core",
        name="Core",
        id=42,
        org="acme",
        description="Core team",
        privacy="closed",
        members=["alice"],
        repositories=["acme/widget"],
    )
    assert team.type == GitHubItemType.TEAM
    assert team.full_name == "acme/core"
    assert team.parent is None

    graph = GraphData()
    graph.add_team(team)
    team_key = "https://github.com/orgs/acme/teams/core"
    assert graph.has_team(team_key)
    assert graph.get_team(team_key).slug == "core"


def test_org_model():
    """Test OrgModel creation."""
    org = OrgModel(
        login="testorg",
        name="Test Organization",
        id=456,
        type=GitHubItemType.ORGANIZATION
    )
    assert org.login == "testorg"
    assert len(org.members) == 0


def test_repo_model():
    """Test RepoModel creation."""
    repo = RepoModel(
        full_name="user/repo",
        name="repo",
        id=789,
        owner="user",
        is_fork=False
    )
    assert repo.full_name == "user/repo"
    assert repo.owner == "user"
    assert not repo.is_fork


def test_graph_data():
    """Test GraphData operations."""
    graph = GraphData()
    
    # Add user — GraphData is keyed by canonical URL.
    user = UserModel(login="user1", id=1)
    graph.add_user(user)
    assert graph.has_user("https://github.com/user1")
    assert graph.get_user("https://github.com/user1").login == "user1"

    # Add org
    org = OrgModel(login="org1", id=2)
    graph.add_org(org)
    assert graph.has_org("https://github.com/org1")

    # Add repo
    repo = RepoModel(full_name="user1/repo1", id=3, owner="user1")
    graph.add_repo(repo)
    assert graph.has_repo("https://github.com/user1/repo1")
    
    # Check counts
    assert len(graph.users) == 1
    assert len(graph.orgs) == 1
    assert len(graph.repos) == 1


def test_graph_data_get_methods():
    """Test GraphData get methods."""
    graph = GraphData()
    
    # Non-existent entities
    assert graph.get_user("nonexistent") is None
    assert graph.get_org("nonexistent") is None
    assert graph.get_repo("nonexistent") is None


def test_fork_repository():
    """Test fork repository model."""
    parent_repo = RepoModel(
        full_name="original/repo",
        name="repo",
        id=100,
        owner="original",
        is_fork=False
    )
    
    forked_repo = RepoModel(
        full_name="user/repo",
        name="repo",
        id=101,
        owner="user",
        is_fork=True,
        forked_from="original/repo"
    )
    
    assert forked_repo.is_fork
    assert forked_repo.forked_from == "original/repo"
    assert not parent_repo.is_fork


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
