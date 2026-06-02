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


# --- Infoscience subkinds (Spec 3) ---------------------------------
from open_pulse_crawler.models import (
    InfoscienceItem, InfosciencePerson, InfoscienceOrgUnit,
)


def test_infoscience_item_subkind_and_fields():
    item = InfoscienceItem(
        url="https://infoscience.epfl.ch/handle/20.500.14299/182247",
        full_name="20.500.14299/182247",
        platform="infoscience",
        handle="20.500.14299/182247",
        uuid="80f7da77-dc21-430e-88a2-f07ede2cb194",
        resource_type="master thesis",
        title="A study of X",
        publication_date="2024-06-30",
        authors=[
            {"name": "Doe, J.", "orcid": "0000-0001-2345-6789",
             "authority_uuid": "31b1115e-c04a-445d-b905-18616ef2aacb"},
        ],
        keywords=["urban planning", "thesis"],
        license="CC-BY-4.0",
    )
    assert item.subkind == "InfoscienceItem"
    assert item.handle == "20.500.14299/182247"
    assert item.uuid == "80f7da77-dc21-430e-88a2-f07ede2cb194"
    assert item.resource_type == "master thesis"
    assert item.is_fork is False  # inherited; no DSpace fork concept
    assert item.dependents == []  # inherited; not a DSpace concept


def test_infoscience_person_subkind_and_fields():
    person = InfosciencePerson(
        url="https://infoscience.epfl.ch/handle/20.500.14299/99923",
        login="123456",  # SciPer ID
        platform="infoscience",
        handle="20.500.14299/99923",
        uuid="31b1115e-c04a-445d-b905-18616ef2aacb",
        given_name="Nicholas",
        family_name="Molyneaux",
        orcid="0000-0001-2345-6789",
        sciper_id="123456",
        affiliation_name="TRANSP-OR",
        affiliation_uuid="aaaa1111-2222-3333-4444-555566667777",
    )
    assert person.subkind == "InfosciencePerson"
    assert person.handle == "20.500.14299/99923"
    assert person.sciper_id == "123456"
    assert person.orcid == "0000-0001-2345-6789"
    assert person.followers == []  # Infoscience has no social graph


def test_infoscience_orgunit_subkind_and_fields():
    ou = InfoscienceOrgUnit(
        url="https://infoscience.epfl.ch/handle/20.500.14299/77777",
        login="TRANSP-OR",
        platform="infoscience",
        handle="20.500.14299/77777",
        uuid="aaaa1111-2222-3333-4444-555566667777",
        unit_id="TRANSP-OR",
        parent_uuid="bbbb2222-3333-4444-5555-666677778888",
        parent_url="https://infoscience.epfl.ch/handle/20.500.14299/11111",
        unit_type="laboratory",
    )
    assert ou.subkind == "InfoscienceOrgUnit"
    assert ou.parent_uuid == "bbbb2222-3333-4444-5555-666677778888"
    assert ou.unit_type == "laboratory"


def test_graphdata_accepts_infoscience_subclasses_in_existing_dicts():
    from open_pulse_crawler.models import GraphData
    g = GraphData()
    g.users["https://infoscience.epfl.ch/handle/20.500.14299/99923"] = InfosciencePerson(
        url="https://infoscience.epfl.ch/handle/20.500.14299/99923",
        login="123456", platform="infoscience",
        handle="20.500.14299/99923",
        uuid="31b1115e-c04a-445d-b905-18616ef2aacb",
    )
    g.orgs["https://infoscience.epfl.ch/handle/20.500.14299/77777"] = InfoscienceOrgUnit(
        url="https://infoscience.epfl.ch/handle/20.500.14299/77777",
        login="TRANSP-OR", platform="infoscience",
        handle="20.500.14299/77777",
        uuid="aaaa1111-2222-3333-4444-555566667777",
    )
    g.repos["https://infoscience.epfl.ch/handle/20.500.14299/182247"] = InfoscienceItem(
        url="https://infoscience.epfl.ch/handle/20.500.14299/182247",
        full_name="20.500.14299/182247", platform="infoscience",
        handle="20.500.14299/182247",
        uuid="80f7da77-dc21-430e-88a2-f07ede2cb194",
    )
    restored = GraphData.model_validate_json(g.model_dump_json())
    assert isinstance(restored.users["https://infoscience.epfl.ch/handle/20.500.14299/99923"], InfosciencePerson)
    assert isinstance(restored.orgs["https://infoscience.epfl.ch/handle/20.500.14299/77777"], InfoscienceOrgUnit)
    assert isinstance(restored.repos["https://infoscience.epfl.ch/handle/20.500.14299/182247"], InfoscienceItem)


def test_infoscience_item_typed_relations_and_affiliations():
    item = InfoscienceItem(
        url="https://infoscience.epfl.ch/handle/20.500.14299/1",
        full_name="20.500.14299/1", platform="infoscience",
        handle="20.500.14299/1", uuid="x",
        affiliations=[{"name": "TRANSP-OR", "authority_uuid": "ou-1"}],
        relations=[{"qualifier": "isversionof", "value": "10.5281/zenodo.99"}],
    )
    assert item.affiliations[0]["authority_uuid"] == "ou-1"
    assert item.relations[0]["qualifier"] == "isversionof"


# --- DataCite subkinds (Spec 4) ------------------------------------
from open_pulse_crawler.models import (
    DataCiteWork, DataCiteOrganization, DataCitePerson, DataCiteClient,
)


def test_datacite_work_subkind_and_fields():
    work = DataCiteWork(
        url="https://doi.org/10.6084/m9.figshare.99",
        full_name="10.6084/m9.figshare.99",
        platform="datacite",
        doi="10.6084/m9.figshare.99",
        resource_type="Dataset",
        resource_type_detail="Tabular dataset",
        title="My dataset",
        publication_year=2024,
        publisher="figshare",
        client_id="figshare.ars",
        creators=[
            {"name": "Doe, J.", "orcid": "0000-0001-2345-6789",
             "affiliations": [{"name": "EPFL", "ror": "02s376052", "scheme": "ROR"}]},
        ],
        affiliations=[{"name": "EPFL", "ror": "02s376052", "scheme": "ROR"}],
        relations=[{"relation_type": "IsSupplementTo", "target_type": "URL",
                    "target": "https://github.com/foo/bar"}],
        subjects=["genomics", "open data"],
        abstract="A short description.",
        container_title="Nature",
        language="en",
        registered_url="https://figshare.com/articles/dataset/My_dataset/99",
    )
    assert work.subkind == "DataCiteWork"
    assert work.doi == "10.6084/m9.figshare.99"
    assert work.publication_year == 2024
    assert work.client_id == "figshare.ars"
    assert work.is_fork is False  # inherited; no DataCite fork concept
    assert work.dependents == []


def test_datacite_organization_subkind_and_fields():
    org = DataCiteOrganization(
        url="https://ror.org/02s376052",
        login="02s376052",
        platform="datacite",
        ror_id="02s376052",
        ror_url="https://ror.org/02s376052",
    )
    assert org.subkind == "DataCiteOrganization"
    assert org.ror_id == "02s376052"
    assert org.members == []  # bare anchor — never populated


def test_datacite_person_subkind_and_fields():
    person = DataCitePerson(
        url="https://orcid.org/0000-0002-1825-0097",
        login="0000-0002-1825-0097",
        platform="datacite",
        orcid="0000-0002-1825-0097",
        orcid_url="https://orcid.org/0000-0002-1825-0097",
    )
    assert person.subkind == "DataCitePerson"
    assert person.orcid == "0000-0002-1825-0097"
    assert person.followers == []  # bare anchor — DataCite has no social graph


def test_datacite_client_subkind_and_fields():
    client = DataCiteClient(
        url="https://commons.datacite.org/repositories/cern.zenodo",
        login="cern.zenodo",
        platform="datacite",
        client_id="cern.zenodo",
        repository_name="Zenodo",
        alternate_name="Research. Shared",
        client_type="repository",
        repository_type=["disciplinary"],
        description="ZENODO builds and operates a simple and innovative service…",
        repository_url="https://zenodo.org/",
        domains=["openaire.cern.ch", "zenodo.org"],
        re3data_doi="https://doi.org/10.17616/R3QP53",
        year_registered=2013,
        is_active=True,
        doi_prefixes=["10.5281", "10.5072"],
    )
    assert client.subkind == "DataCiteClient"
    assert client.client_id == "cern.zenodo"
    assert "zenodo.org" in client.domains
    assert client.is_active is True


def test_graphdata_accepts_datacite_subclasses_in_existing_dicts():
    from open_pulse_crawler.models import GraphData
    g = GraphData()
    g.users["https://orcid.org/0000-0002-1825-0097"] = DataCitePerson(
        url="https://orcid.org/0000-0002-1825-0097",
        login="0000-0002-1825-0097", platform="datacite",
        orcid="0000-0002-1825-0097",
        orcid_url="https://orcid.org/0000-0002-1825-0097",
    )
    g.orgs["https://ror.org/02s376052"] = DataCiteOrganization(
        url="https://ror.org/02s376052",
        login="02s376052", platform="datacite",
        ror_id="02s376052",
        ror_url="https://ror.org/02s376052",
    )
    g.orgs["https://commons.datacite.org/repositories/cern.zenodo"] = DataCiteClient(
        url="https://commons.datacite.org/repositories/cern.zenodo",
        login="cern.zenodo", platform="datacite",
        client_id="cern.zenodo",
    )
    g.repos["https://doi.org/10.6084/m9.figshare.99"] = DataCiteWork(
        url="https://doi.org/10.6084/m9.figshare.99",
        full_name="10.6084/m9.figshare.99", platform="datacite",
        doi="10.6084/m9.figshare.99",
    )
    restored = GraphData.model_validate_json(g.model_dump_json())
    assert isinstance(restored.users["https://orcid.org/0000-0002-1825-0097"], DataCitePerson)
    assert isinstance(restored.orgs["https://ror.org/02s376052"], DataCiteOrganization)
    assert isinstance(restored.orgs["https://commons.datacite.org/repositories/cern.zenodo"], DataCiteClient)
    assert isinstance(restored.repos["https://doi.org/10.6084/m9.figshare.99"], DataCiteWork)



# --- HuggingFace subkinds (Spec 5) -------------------------------
from open_pulse_crawler.models import (
    HuggingFaceUser, HuggingFaceOrg, HuggingFaceRepo,
    HuggingFacePaper, HuggingFaceCollection,
)


def test_huggingface_user_subkind_and_fields():
    user = HuggingFaceUser(
        url="https://huggingface.co/karpathy",
        login="karpathy",
        platform="huggingface",
        username="karpathy",
        fullname="Andrej Karpathy",
        is_pro=False,
        avatar_url="https://cdn.huggingface.co/avatars/karpathy.png",
        num_models=30,
        num_datasets=5,
        num_spaces=2,
        num_papers=12,
        num_followers=80000,
        member_orgs=["nanoGPT"],
    )
    assert user.subkind == "HuggingFaceUser"
    assert user.username == "karpathy"
    assert user.num_models == 30
    assert user.followers == []  # inherited from UserModel; HF has own counter


def test_huggingface_org_subkind_and_fields():
    org = HuggingFaceOrg(
        url="https://huggingface.co/meta-llama",
        login="meta-llama",
        platform="huggingface",
        org_name="meta-llama",
        fullname="Meta Llama",
        is_verified=True,
        plan="enterprise",
        num_models=80,
        num_datasets=10,
        num_followers=5000,
    )
    assert org.subkind == "HuggingFaceOrg"
    assert org.is_verified is True
    assert org.members == []  # inherited; populated via expand if has_member ever ships


def test_huggingface_repo_model_subkind_and_fields():
    repo = HuggingFaceRepo(
        url="https://huggingface.co/meta-llama/Llama-3.2-1B",
        full_name="meta-llama/Llama-3.2-1B",
        platform="huggingface",
        repo_type="model",
        repo_id="meta-llama/Llama-3.2-1B",
        owner="meta-llama",
        repo_name="Llama-3.2-1B",
        sha="abc123",
        tags=["transformers", "llama-3", "text-generation"],
        downloads=2222053,
        likes=2412,
        license="llama3.2",
        language=["en"],
        gated=True,
        pipeline_tag="text-generation",
        library_name="transformers",
    )
    assert repo.subkind == "HuggingFaceRepo"
    assert repo.repo_type == "model"
    assert repo.pipeline_tag == "text-generation"
    # Space-only and dataset-only fields stay empty for models
    assert repo.sdk == ""
    assert repo.used_models == []
    assert repo.paperswithcode_id == ""


def test_huggingface_repo_dataset_subkind_and_fields():
    repo = HuggingFaceRepo(
        url="https://huggingface.co/datasets/openai/gsm8k",
        full_name="datasets/openai/gsm8k",
        platform="huggingface",
        repo_type="dataset",
        repo_id="openai/gsm8k",
        owner="openai",
        repo_name="gsm8k",
        paperswithcode_id="gsm8k",
    )
    assert repo.repo_type == "dataset"
    assert repo.paperswithcode_id == "gsm8k"
    assert repo.pipeline_tag == ""


def test_huggingface_repo_space_subkind_and_fields():
    repo = HuggingFaceRepo(
        url="https://huggingface.co/spaces/black-forest-labs/FLUX.1-schnell",
        full_name="spaces/black-forest-labs/FLUX.1-schnell",
        platform="huggingface",
        repo_type="space",
        repo_id="black-forest-labs/FLUX.1-schnell",
        owner="black-forest-labs",
        repo_name="FLUX.1-schnell",
        sdk="gradio",
        runtime_stage="RUNNING",
        used_models=["black-forest-labs/FLUX.1-schnell"],
        likes=5067,
    )
    assert repo.repo_type == "space"
    assert repo.sdk == "gradio"
    assert repo.runtime_stage == "RUNNING"
    assert "black-forest-labs/FLUX.1-schnell" in repo.used_models


def test_huggingface_paper_subkind_and_fields():
    paper = HuggingFacePaper(
        url="https://huggingface.co/papers/2307.09288",
        full_name="papers/2307.09288",
        platform="huggingface",
        arxiv_id="2307.09288",
        arxiv_url="https://arxiv.org/abs/2307.09288",
        title="Llama 2: Open Foundation and Fine-Tuned Chat Models",
        summary="Long-form abstract.",
        ai_summary="Short AI-generated TL;DR.",
        ai_keywords=["llm", "fine-tuning", "instruction-tuning"],
        authors=[{"name": "Hugo Touvron"}, {"name": "Louis Martin"}],
        upvotes=252,
        published_at="2023-07-18T00:00:00Z",
        github_repo="facebookresearch/llama",
        num_linked_models=8,
        num_linked_datasets=2,
        num_linked_spaces=14,
    )
    assert paper.subkind == "HuggingFacePaper"
    assert paper.arxiv_id == "2307.09288"
    assert paper.github_repo == "facebookresearch/llama"
    assert paper.num_linked_models == 8


def test_huggingface_collection_subkind_and_fields():
    coll = HuggingFaceCollection(
        url="https://huggingface.co/collections/meta-llama/metas-llama-32-language-models-and-evals-675bfd70e574a62dd0e40586",
        login="meta-llama/metas-llama-32-language-models-and-evals-675bfd70e574a62dd0e40586",
        platform="huggingface",
        slug="meta-llama/metas-llama-32-language-models-and-evals-675bfd70e574a62dd0e40586",
        owner="meta-llama",
        title="Meta's Llama 3.2 language models & evals",
        description="The Llama 3.2 release.",
        upvotes=120,
        last_updated="2024-12-01T00:00:00Z",
    )
    assert coll.subkind == "HuggingFaceCollection"
    assert coll.owner == "meta-llama"
    assert coll.title.startswith("Meta's Llama 3.2")


def test_graphdata_accepts_huggingface_subclasses_in_existing_dicts():
    from open_pulse_crawler.models import GraphData
    g = GraphData()
    g.users["https://huggingface.co/karpathy"] = HuggingFaceUser(
        url="https://huggingface.co/karpathy",
        login="karpathy", platform="huggingface",
        username="karpathy",
    )
    g.orgs["https://huggingface.co/meta-llama"] = HuggingFaceOrg(
        url="https://huggingface.co/meta-llama",
        login="meta-llama", platform="huggingface",
        org_name="meta-llama",
    )
    g.orgs["https://huggingface.co/collections/meta-llama/llama-32-...x"] = HuggingFaceCollection(
        url="https://huggingface.co/collections/meta-llama/llama-32-...x",
        login="meta-llama/llama-32-...x", platform="huggingface",
        slug="meta-llama/llama-32-...x",
        owner="meta-llama",
    )
    g.repos["https://huggingface.co/meta-llama/Llama-3.2-1B"] = HuggingFaceRepo(
        url="https://huggingface.co/meta-llama/Llama-3.2-1B",
        full_name="meta-llama/Llama-3.2-1B", platform="huggingface",
        repo_type="model",
        repo_id="meta-llama/Llama-3.2-1B",
        owner="meta-llama",
        repo_name="Llama-3.2-1B",
    )
    g.repos["https://huggingface.co/papers/2307.09288"] = HuggingFacePaper(
        url="https://huggingface.co/papers/2307.09288",
        full_name="papers/2307.09288", platform="huggingface",
        arxiv_id="2307.09288",
        arxiv_url="https://arxiv.org/abs/2307.09288",
    )
    restored = GraphData.model_validate_json(g.model_dump_json())
    assert isinstance(restored.users["https://huggingface.co/karpathy"], HuggingFaceUser)
    assert isinstance(restored.orgs["https://huggingface.co/meta-llama"], HuggingFaceOrg)
    assert isinstance(restored.orgs["https://huggingface.co/collections/meta-llama/llama-32-...x"], HuggingFaceCollection)
    assert isinstance(restored.repos["https://huggingface.co/meta-llama/Llama-3.2-1B"], HuggingFaceRepo)
    assert isinstance(restored.repos["https://huggingface.co/papers/2307.09288"], HuggingFacePaper)


# --- CrossrefWork subkind (Spec 6 foundations) --------------------------------

def test_crossref_work_subkind_and_minimal_fields():
    from open_pulse_crawler.models import CrossrefWork
    work = CrossrefWork(
        url="https://doi.org/10.1038/s41586-023-06837-4",
        full_name="10.1038/s41586-023-06837-4",
        platform="crossref",
        doi="10.1038/s41586-023-06837-4",
    )
    assert work.subkind == "CrossrefWork"
    assert work.doi == "10.1038/s41586-023-06837-4"
    # default values
    assert work.title == ""
    assert work.publication_year is None
    assert work.publisher == ""
    assert work.container_title == ""
    assert work.work_type == ""
    assert work.abstract == ""
    assert work.creators == []
    assert work.subjects == []
    assert work.funders == []
    assert work.relations == []
    assert work.is_referenced_by_count is None
    assert work.references == []
    assert work.reference_dois == []


def test_crossref_work_all_fields():
    from open_pulse_crawler.models import CrossrefWork
    work = CrossrefWork(
        url="https://doi.org/10.1038/s41586-023-06837-4",
        full_name="10.1038/s41586-023-06837-4",
        platform="crossref",
        doi="10.1038/s41586-023-06837-4",
        title="A landmark Nature paper",
        publication_year=2023,
        publisher="Nature Publishing Group",
        container_title="Nature",
        work_type="journal-article",
        abstract="We describe a new method…",
        creators=[{"given": "Jane", "family": "Doe", "orcid": "0000-0001-2345-6789"}],
        subjects=["biology", "open science"],
        funders=[{"name": "Wellcome Trust", "doi": "10.13039/100004440"}],
        relations=[{"relation_type": "IsSupplementedBy",
                    "target": "https://github.com/org/repo"}],
        is_referenced_by_count=42,
        references=["https://doi.org/10.1000/xyz123"],
        reference_dois=["10.1000/xyz123"],
    )
    assert work.subkind == "CrossrefWork"
    assert work.title == "A landmark Nature paper"
    assert work.publication_year == 2023
    assert work.is_referenced_by_count == 42
    assert work.is_fork is False  # inherited from RepoModel; no Crossref fork concept


def test_crossref_work_graphdata_round_trip():
    """CrossrefWork survives GraphData model_dump → re-parse (union registration check)."""
    from open_pulse_crawler.models import CrossrefWork, GraphData
    doi_url = "https://doi.org/10.1038/s41586-023-06837-4"
    g = GraphData()
    g.add_repo(CrossrefWork(
        url=doi_url,
        full_name="10.1038/s41586-023-06837-4",
        platform="crossref",
        doi="10.1038/s41586-023-06837-4",
        title="Round-trip test paper",
        publication_year=2023,
        publisher="Nature Publishing Group",
    ))
    restored = GraphData.model_validate_json(g.model_dump_json())
    assert doi_url in restored.repos
    assert isinstance(restored.repos[doi_url], CrossrefWork)
    assert restored.repos[doi_url].subkind == "CrossrefWork"
    assert restored.repos[doi_url].title == "Round-trip test paper"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])


# --- OpenAlex subkinds (Spec 7) -----------------------------------------------

from open_pulse_crawler.models import (
    OpenAlexWork, OpenAlexAuthor, OpenAlexInstitution,
    OpenAlexSource, OpenAlexFunder,
    ExternalIdentifier,
)


def test_openalex_work_subkind_and_defaults():
    work = OpenAlexWork(
        url="https://openalex.org/W2741809807",
        full_name="W2741809807",
        platform="openalex",
        doi="10.7717/peerj.4375",
        openalex_id="W2741809807",
        title="The state of OA",
        publication_year=2018,
        work_type="journal-article",
        cited_by_count=150,
        is_oa=True,
    )
    assert work.subkind == "OpenAlexWork"
    assert work.doi == "10.7717/peerj.4375"
    assert work.openalex_id == "W2741809807"
    assert work.title == "The state of OA"
    assert work.publication_year == 2018
    assert work.work_type == "journal-article"
    assert work.cited_by_count == 150
    assert work.is_oa is True
    # list defaults
    assert work.references == []
    assert work.cited_by == []
    assert work.authored_by == []
    assert work.funded_by == []
    assert work.creators == []
    assert work.published_in == ""
    # inherited from RepoModel
    assert work.is_fork is False
    assert work.dependents == []


def test_openalex_work_minimal_fields():
    work = OpenAlexWork(
        url="https://openalex.org/W9999",
        full_name="W9999",
        platform="openalex",
    )
    assert work.subkind == "OpenAlexWork"
    assert work.doi == ""
    assert work.openalex_id == ""
    assert work.publication_year is None
    assert work.cited_by_count is None
    assert work.is_oa is None


def test_openalex_author_subkind_and_fields():
    author = OpenAlexAuthor(
        url="https://orcid.org/0000-0002-3336-0163",
        login="0000-0002-3336-0163",
        platform="openalex",
        orcid="0000-0002-3336-0163",
        openalex_id="A5023888391",
    )
    assert author.subkind == "OpenAlexAuthor"
    assert author.orcid == "0000-0002-3336-0163"
    assert author.openalex_id == "A5023888391"
    assert author.affiliations == []
    # inherited from UserModel
    assert author.followers == []


def test_openalex_author_orcid_url_login():
    """Author keyed by ORCID URL with login derived from URL."""
    author = OpenAlexAuthor(
        url="https://orcid.org/0000-0002-3336-0163",
        login="0000-0002-3336-0163",
        platform="openalex",
    )
    assert author.url == "https://orcid.org/0000-0002-3336-0163"
    assert author.login == "0000-0002-3336-0163"


def test_openalex_institution_subkind_and_fields():
    inst = OpenAlexInstitution(
        url="https://ror.org/02s376052",
        login="02s376052",
        platform="openalex",
        ror_id="02s376052",
        openalex_id="I209863525",
        country_code="CH",
        institution_type="education",
    )
    assert inst.subkind == "OpenAlexInstitution"
    assert inst.ror_id == "02s376052"
    assert inst.openalex_id == "I209863525"
    assert inst.country_code == "CH"
    assert inst.institution_type == "education"
    # inherited from OrgModel
    assert inst.members == []


def test_openalex_institution_minimal():
    inst = OpenAlexInstitution(
        url="https://ror.org/00abc123",
        login="00abc123",
        platform="openalex",
    )
    assert inst.subkind == "OpenAlexInstitution"
    assert inst.ror_id == ""
    assert inst.openalex_id == ""
    assert inst.country_code == ""
    assert inst.institution_type == ""


def test_openalex_source_subkind_and_fields():
    src = OpenAlexSource(
        url="https://openalex.org/S137773608",
        login="S137773608",
        platform="openalex",
        openalex_id="S137773608",
        issn_l="2167-8359",
        issns=["2167-8359"],
        host_organization="https://openalex.org/P4310320595",
        is_oa=True,
    )
    assert src.subkind == "OpenAlexSource"
    assert src.openalex_id == "S137773608"
    assert src.issn_l == "2167-8359"
    assert src.issns == ["2167-8359"]
    assert src.host_organization == "https://openalex.org/P4310320595"
    assert src.is_oa is True
    # list defaults
    assert src.members == []


def test_openalex_source_minimal():
    src = OpenAlexSource(
        url="https://openalex.org/S999",
        login="S999",
        platform="openalex",
    )
    assert src.subkind == "OpenAlexSource"
    assert src.issns == []
    assert src.is_oa is None


def test_openalex_funder_subkind_and_fields():
    funder = OpenAlexFunder(
        url="https://openalex.org/F4320306076",
        login="F4320306076",
        platform="openalex",
        openalex_id="F4320306076",
        funder_doi="10.13039/501100001659",
        country_code="DE",
    )
    assert funder.subkind == "OpenAlexFunder"
    assert funder.openalex_id == "F4320306076"
    assert funder.funder_doi == "10.13039/501100001659"
    assert funder.country_code == "DE"
    # inherited from OrgModel
    assert funder.members == []


def test_openalex_funder_minimal():
    funder = OpenAlexFunder(
        url="https://openalex.org/F9999",
        login="F9999",
        platform="openalex",
    )
    assert funder.subkind == "OpenAlexFunder"
    assert funder.funder_doi == ""
    assert funder.country_code == ""


def test_graphdata_openalex_full_roundtrip():
    """All 5 OpenAlex subkinds survive GraphData JSON round-trip (union registration)."""
    from open_pulse_crawler.models import GraphData

    work_url = "https://openalex.org/W2741809807"
    author_url = "https://orcid.org/0000-0002-3336-0163"
    inst_url = "https://ror.org/02s376052"
    src_url = "https://openalex.org/S137773608"
    funder_url = "https://openalex.org/F4320306076"

    g = GraphData()

    g.add_repo(OpenAlexWork(
        url=work_url,
        full_name="W2741809807",
        platform="openalex",
        doi="10.7717/peerj.4375",
        openalex_id="W2741809807",
        title="The state of OA",
        publication_year=2018,
        work_type="journal-article",
        cited_by_count=150,
        is_oa=True,
        references=["https://openalex.org/W1111"],
        authored_by=[author_url],
        funded_by=[funder_url],
        published_in=src_url,
        creators=[{"name": "Piwowar, H.", "orcid": "0000-0003-1613-5981",
                   "institutions": ["https://ror.org/02s376052"]}],
        external_identifiers=[ExternalIdentifier(scheme="pmid", value="42")],
    ))

    g.add_user(OpenAlexAuthor(
        url=author_url,
        login="0000-0002-3336-0163",
        platform="openalex",
        orcid="0000-0002-3336-0163",
        openalex_id="A5023888391",
        affiliations=[inst_url],
    ))

    g.add_org(OpenAlexInstitution(
        url=inst_url,
        login="02s376052",
        platform="openalex",
        ror_id="02s376052",
        openalex_id="I209863525",
        country_code="CH",
        institution_type="education",
    ))

    g.add_org(OpenAlexSource(
        url=src_url,
        login="S137773608",
        platform="openalex",
        openalex_id="S137773608",
        issn_l="2167-8359",
        issns=["2167-8359"],
        host_organization="https://openalex.org/P4310320595",
        is_oa=True,
    ))

    g.add_org(OpenAlexFunder(
        url=funder_url,
        login="F4320306076",
        platform="openalex",
        openalex_id="F4320306076",
        funder_doi="10.13039/501100001659",
        country_code="DE",
    ))

    restored = GraphData.model_validate_json(g.model_dump_json())

    assert isinstance(restored.repos[work_url], OpenAlexWork)
    assert isinstance(restored.users[author_url], OpenAlexAuthor)
    assert isinstance(restored.orgs[inst_url], OpenAlexInstitution)
    assert isinstance(restored.orgs[src_url], OpenAlexSource)
    assert isinstance(restored.orgs[funder_url], OpenAlexFunder)

    # verify field values survive round-trip
    restored_work = restored.repos[work_url]
    assert restored_work.subkind == "OpenAlexWork"
    assert restored_work.doi == "10.7717/peerj.4375"
    assert restored_work.title == "The state of OA"
    assert restored_work.cited_by_count == 150
    assert restored_work.is_oa is True
    assert restored_work.references == ["https://openalex.org/W1111"]
    assert restored_work.authored_by == [author_url]
    assert restored_work.funded_by == [funder_url]
    assert restored_work.published_in == src_url
    assert len(restored_work.creators) == 1
    assert restored_work.creators[0]["name"] == "Piwowar, H."

    # external_identifiers survive round-trip
    assert len(restored_work.external_identifiers) == 1
    assert restored_work.external_identifiers[0].scheme == "pmid"
    assert restored_work.external_identifiers[0].value == "42"

    restored_author = restored.users[author_url]
    assert restored_author.subkind == "OpenAlexAuthor"
    assert restored_author.orcid == "0000-0002-3336-0163"
    assert restored_author.affiliations == [inst_url]

    restored_inst = restored.orgs[inst_url]
    assert restored_inst.subkind == "OpenAlexInstitution"
    assert restored_inst.ror_id == "02s376052"
    assert restored_inst.country_code == "CH"

    restored_src = restored.orgs[src_url]
    assert restored_src.subkind == "OpenAlexSource"
    assert restored_src.issn_l == "2167-8359"

    restored_funder = restored.orgs[funder_url]
    assert restored_funder.subkind == "OpenAlexFunder"
    assert restored_funder.funder_doi == "10.13039/501100001659"
