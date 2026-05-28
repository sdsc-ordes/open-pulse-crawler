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


# --- Zenodo subkinds (Spec 2) --------------------------------------
from open_pulse_crawler.models import (
    ZenodoUserModel, ZenodoCommunityModel, ZenodoRecordModel,
    UserModel, OrgModel, RepoModel, GraphData,
)


def test_zenodo_user_subkind_and_fields():
    u = ZenodoUserModel(
        url="https://zenodo.org/users/12345",
        login="alice",
        platform="zenodo",
        id=12345,
        orcid="0000-0001-2345-6789",
        affiliation="EPFL",
    )
    assert u.subkind == "ZenodoUser"
    assert u.orcid == "0000-0001-2345-6789"
    assert u.affiliation == "EPFL"
    assert u.followers == []  # Zenodo has no social graph


def test_zenodo_community_subkind_and_fields():
    c = ZenodoCommunityModel(
        url="https://zenodo.org/communities/sdsc-ordes",
        login="sdsc-ordes",
        platform="zenodo",
        community_type="organization",
        description="Swiss Data Science Center — ORDES",
    )
    assert c.subkind == "ZenodoCommunity"
    assert c.community_type == "organization"
    assert c.members == []  # members out of scope


def test_zenodo_record_subkind_concept_self_reference():
    r = ZenodoRecordModel(
        url="https://zenodo.org/records/7234562",
        full_name="10.5281/zenodo.7234562",
        platform="zenodo",
        doi="10.5281/zenodo.7234562",
        concept_doi="10.5281/zenodo.7234562",  # self-reference: this record IS the concept
        title="Renku — A Platform for Reproducible Data Science",
        resource_type="software",
        license="Apache-2.0",
    )
    assert r.subkind == "ZenodoRecord"
    assert r.doi == r.concept_doi  # convention: self-reference for non-versioned
    assert r.is_fork is False  # inherited from RepoModel
    assert r.dependents == []   # not a Zenodo concept


def test_zenodo_record_with_version_chain():
    r = ZenodoRecordModel(
        url="https://zenodo.org/records/7234500",
        full_name="10.5281/zenodo.7234500",
        platform="zenodo",
        doi="10.5281/zenodo.7234500",
        concept_doi="10.5281/zenodo.7234500",
        latest_version="2.0.0",
        latest_version_doi="10.5281/zenodo.7234562",
        latest_version_url="https://zenodo.org/records/7234562",
        versions=[
            {"doi": "10.5281/zenodo.7234500", "url": "https://zenodo.org/records/7234500",
             "version": "1.0.0", "publication_date": "2023-01-15", "record_id": "7234500"},
            {"doi": "10.5281/zenodo.7234562", "url": "https://zenodo.org/records/7234562",
             "version": "2.0.0", "publication_date": "2024-06-30", "record_id": "7234562"},
        ],
    )
    assert len(r.versions) == 2
    assert r.latest_version == "2.0.0"


def test_zenodo_record_creators_embedded_dicts():
    """Creators are author names + ORCIDs, NOT crawl-able User nodes (identity resolution out of scope)."""
    r = ZenodoRecordModel(
        url="https://zenodo.org/records/9",
        full_name="10.5281/zenodo.9",
        platform="zenodo",
        doi="10.5281/zenodo.9",
        concept_doi="10.5281/zenodo.9",
        creators=[
            {"name": "Bovel, Matthieu", "orcid": "0000-0001-1111-1111", "affiliation": "EPFL"},
            {"name": "Doe, Jane", "affiliation": "ETHZ"},
        ],
    )
    assert len(r.creators) == 2
    assert r.creators[0]["orcid"] == "0000-0001-1111-1111"


def test_graphdata_accepts_zenodo_subclasses_in_existing_dicts():
    """Discriminated unions must accept Zenodo subkinds alongside GitHub/GitLab ones."""
    g = GraphData()
    g.users["https://zenodo.org/users/1"] = ZenodoUserModel(
        url="https://zenodo.org/users/1", login="u1", platform="zenodo",
    )
    g.orgs["https://zenodo.org/communities/c"] = ZenodoCommunityModel(
        url="https://zenodo.org/communities/c", login="c", platform="zenodo",
    )
    g.repos["https://zenodo.org/records/1"] = ZenodoRecordModel(
        url="https://zenodo.org/records/1", full_name="10.5281/zenodo.1",
        platform="zenodo", doi="10.5281/zenodo.1", concept_doi="10.5281/zenodo.1",
    )

    payload = g.model_dump_json()
    restored = GraphData.model_validate_json(payload)

    assert isinstance(restored.users["https://zenodo.org/users/1"], ZenodoUserModel)
    assert isinstance(restored.orgs["https://zenodo.org/communities/c"], ZenodoCommunityModel)
    assert isinstance(restored.repos["https://zenodo.org/records/1"], ZenodoRecordModel)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
