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


# --- v3: subkind discriminator + GitLab subclasses --------------------------
from open_pulse_crawler.models import (
    ExternalIdentifier, GitLabUserModel, GitLabGroupModel, GitLabProjectModel,
    GRAPH_SCHEMA_VERSION,
)


def test_existing_models_have_subkind():
    u = UserModel(url="https://github.com/torvalds", login="torvalds")
    assert u.subkind == "GitHubUser"
    o = OrgModel(url="https://github.com/anthropic", login="anthropic")
    assert o.subkind == "GitHubOrganization"
    r = RepoModel(url="https://github.com/owner/repo", full_name="owner/repo")
    assert r.subkind == "GitHubRepository"
    t = TeamModel(url="https://github.com/orgs/anthropic/teams/core",
                  full_name="anthropic/core", org="anthropic", slug="core")
    assert t.subkind == "GitHubTeam"


def test_gitlab_user_subkind_and_fields():
    u = GitLabUserModel(
        url="https://gitlab.epfl.ch/alice", login="alice",
        platform="gitlab", state="active", public_email="alice@example.org",
    )
    assert u.subkind == "GitLabUser"
    assert u.state == "active"
    assert u.public_email == "alice@example.org"


def test_gitlab_group_subkind_and_parent():
    g = GitLabGroupModel(
        url="https://gitlab.epfl.ch/parent/child", login="parent/child",
        platform="gitlab",
        parent="https://gitlab.epfl.ch/parent",
        visibility="internal",
    )
    assert g.subkind == "GitLabGroup"
    assert g.parent == "https://gitlab.epfl.ch/parent"
    assert g.visibility == "internal"


def test_gitlab_project_defaults():
    p = GitLabProjectModel(
        url="https://gitlab.epfl.ch/g/p", full_name="g/p",
        platform="gitlab",
    )
    assert p.subkind == "GitLabProject"
    assert p.visibility == "public"
    assert p.namespace is None


def test_graphdata_roundtrips_gitlab_subclass_via_discriminator():
    g = GraphData()
    p = GitLabProjectModel(
        url="https://gitlab.com/x/y", full_name="x/y", platform="gitlab",
        visibility="private",
    )
    g.repos[p.url] = p
    payload = g.model_dump_json()
    g2 = GraphData.model_validate_json(payload)
    restored = g2.repos["https://gitlab.com/x/y"]
    assert isinstance(restored, GitLabProjectModel)
    assert restored.visibility == "private"


def test_graphdata_roundtrips_mixed_platforms_in_same_dict():
    g = GraphData()
    gh = RepoModel(url="https://github.com/a/b", full_name="a/b")
    gl = GitLabProjectModel(url="https://gitlab.com/x/y", full_name="x/y",
                            platform="gitlab", visibility="internal")
    g.repos[gh.url] = gh
    g.repos[gl.url] = gl
    g2 = GraphData.model_validate_json(g.model_dump_json())
    assert isinstance(g2.repos[gh.url], RepoModel)
    assert not isinstance(g2.repos[gh.url], GitLabProjectModel)
    assert isinstance(g2.repos[gl.url], GitLabProjectModel)


def test_external_identifier_field():
    u = UserModel(
        url="https://github.com/torvalds", login="torvalds",
        external_identifiers=[ExternalIdentifier(scheme="orcid", value="0000-0001-2345-6789")],
    )
    assert u.external_identifiers[0].scheme == "orcid"


def test_extras_field_default_empty():
    u = UserModel(url="https://github.com/x", login="x")
    assert u.extras == {}


def test_extras_field_accepts_arbitrary_data():
    u = UserModel(url="https://github.com/x", login="x",
                  extras={"company": "ACME", "bio": "irrelevant"})
    assert u.extras["company"] == "ACME"


def test_schema_version_bumped_to_3():
    assert GRAPH_SCHEMA_VERSION == 3


def test_graphdata_default_schema_is_3():
    assert GraphData().schema_version == 3


def test_graphdata_of_subkind_filters_across_dicts():
    g = GraphData()
    g.users["https://github.com/a"] = UserModel(url="https://github.com/a", login="a")
    g.users["https://gitlab.com/b"] = GitLabUserModel(
        url="https://gitlab.com/b", login="b", platform="gitlab"
    )
    glu = list(g.of_subkind("GitLabUser"))
    ghu = list(g.of_subkind("GitHubUser"))
    assert len(glu) == 1 and glu[0].login == "b"
    assert len(ghu) == 1 and ghu[0].login == "a"


def test_graphdata_by_platform_groups_correctly():
    g = GraphData()
    g.users["https://github.com/a"] = UserModel(url="https://github.com/a", login="a")
    g.orgs["https://gitlab.com/x"] = GitLabGroupModel(
        url="https://gitlab.com/x", login="x", platform="gitlab",
    )
    assert len(list(g.by_platform("github"))) == 1
    assert len(list(g.by_platform("gitlab"))) == 1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
