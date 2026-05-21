"""Basic tests for the crawler."""

import pytest
from open_pulse_crawler.models import (
    GitHubItemType, UserModel, OrgModel, RepoModel, GraphData
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
    
    # Add user
    user = UserModel(login="user1", id=1)
    graph.add_user(user)
    assert graph.has_user("user1")
    assert graph.get_user("user1").login == "user1"
    
    # Add org
    org = OrgModel(login="org1", id=2)
    graph.add_org(org)
    assert graph.has_org("org1")
    
    # Add repo
    repo = RepoModel(full_name="user1/repo1", id=3, owner="user1")
    graph.add_repo(repo)
    assert graph.has_repo("user1/repo1")
    
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
