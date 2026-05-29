"""Tests for ``GitLabAdapter`` — the concrete PlatformAdapter for GitLab instances.

The adapter wraps a single :class:`GitLabClient` (one per host) and resolves
the user/group/project ambiguity at fetch time. GitLab paths are genuinely
ambiguous at the URL level:

* ``/foo`` could be a user *or* a top-level group;
* ``/a/b`` could be a project ``a/b`` *or* a subgroup ``a/b``.

The adapter probes the GitLab API to disambiguate, caches the result on
``self._kind_cache`` so subsequent ``classify`` / ``fetch`` calls for the
same URI don't re-probe.

Tests mock ``GitLabClient`` so they don't need network access.
"""
from unittest.mock import MagicMock

import pytest

from open_pulse_crawler.models import (
    GitLabGroupModel,
    GitLabProjectModel,
    GitLabUserModel,
)
from open_pulse_crawler.node_id import NodeKind
from open_pulse_crawler.platforms.base import ExpandOpts
from open_pulse_crawler.platforms.gitlab.adapter import GitLabAdapter


@pytest.fixture
def adapter():
    return GitLabAdapter(client=MagicMock(), instance_host="gitlab.epfl.ch")


# ---------- class-var / identity --------------------------------------------


def test_platform_class_var(adapter):
    assert GitLabAdapter.platform == "gitlab"
    assert adapter.instance_host == "gitlab.epfl.ch"


# ---------- normalize_uri ---------------------------------------------------


def test_normalize_uri_strips_trailing_slash(adapter):
    assert adapter.normalize_uri("https://gitlab.epfl.ch/g/p/") == "https://gitlab.epfl.ch/g/p"


# ---------- classify --------------------------------------------------------


def test_classify_top_level_user(adapter):
    raw_user = MagicMock(id=1, username="alice", name="Alice", state="active", public_email="")
    adapter._client.get_user_by_username.return_value = raw_user
    adapter._client.get_group_by_path.return_value = None
    assert adapter.classify("https://gitlab.epfl.ch/alice") == NodeKind.USER_OR_ORG


def test_classify_top_level_group(adapter):
    adapter._client.get_user_by_username.return_value = None
    raw_grp = MagicMock(id=2, full_path="grp")
    adapter._client.get_group_by_path.return_value = raw_grp
    assert adapter.classify("https://gitlab.epfl.ch/grp") == NodeKind.USER_OR_ORG


def test_classify_unknown_top_level_returns_none(adapter):
    adapter._client.get_user_by_username.return_value = None
    adapter._client.get_group_by_path.return_value = None
    assert adapter.classify("https://gitlab.epfl.ch/missing") is None


def test_classify_nested_prefers_project(adapter):
    raw_proj = MagicMock(id=3, path_with_namespace="g/p")
    adapter._client.get_project_by_path.return_value = raw_proj
    assert adapter.classify("https://gitlab.epfl.ch/g/p") == NodeKind.REPO


def test_classify_nested_falls_back_to_subgroup(adapter):
    adapter._client.get_project_by_path.return_value = None
    raw_grp = MagicMock(id=4, full_path="g/sub")
    adapter._client.get_group_by_path.return_value = raw_grp
    assert adapter.classify("https://gitlab.epfl.ch/g/sub") == NodeKind.USER_OR_ORG


def test_classify_caches_result(adapter):
    raw_proj = MagicMock(id=3, path_with_namespace="g/p")
    adapter._client.get_project_by_path.return_value = raw_proj
    # First call probes.
    adapter.classify("https://gitlab.epfl.ch/g/p")
    # Second call should hit cache — no second probe.
    adapter._client.get_project_by_path.reset_mock()
    adapter.classify("https://gitlab.epfl.ch/g/p")
    adapter._client.get_project_by_path.assert_not_called()


# ---------- fetch -----------------------------------------------------------


def test_fetch_user(adapter):
    raw = MagicMock(
        id=10, username="alice", name="Alice",
        state="active", public_email="alice@example.org",
    )
    adapter._client.get_user_by_username.return_value = raw
    adapter._client.get_group_by_path.return_value = None
    node = adapter.fetch("https://gitlab.epfl.ch/alice")
    assert isinstance(node, GitLabUserModel)
    assert node.url == "https://gitlab.epfl.ch/alice"
    assert node.login == "alice"
    assert node.state == "active"
    assert node.public_email == "alice@example.org"
    assert node.platform == "gitlab"


def test_fetch_group(adapter):
    raw = MagicMock(
        id=99, full_path="grp", name="Group",
        visibility="public", parent_id=None,
    )
    adapter._client.get_user_by_username.return_value = None
    adapter._client.get_group_by_path.return_value = raw
    node = adapter.fetch("https://gitlab.epfl.ch/grp")
    assert isinstance(node, GitLabGroupModel)
    assert node.url == "https://gitlab.epfl.ch/grp"
    assert node.login == "grp"
    assert node.visibility == "public"
    assert node.parent is None
    assert node.platform == "gitlab"


def test_fetch_subgroup_with_parent(adapter):
    raw = MagicMock(
        id=100, full_path="grp/sub", name="Sub",
        visibility="internal", parent_id=99,
    )
    adapter._client.get_project_by_path.return_value = None
    adapter._client.get_group_by_path.return_value = raw
    node = adapter.fetch("https://gitlab.epfl.ch/grp/sub")
    assert isinstance(node, GitLabGroupModel)
    assert node.parent == "https://gitlab.epfl.ch/grp"
    assert node.visibility == "internal"


def test_fetch_project(adapter):
    raw = MagicMock(
        id=22, path_with_namespace="g/p", name="p",
        visibility="public", forked_from_project=None,
    )
    raw.namespace = {"kind": "group", "full_path": "g"}
    adapter._client.get_project_by_path.return_value = raw
    node = adapter.fetch("https://gitlab.epfl.ch/g/p")
    assert isinstance(node, GitLabProjectModel)
    assert node.full_name == "g/p"
    assert node.visibility == "public"
    assert node.is_fork is False
    assert node.namespace == "https://gitlab.epfl.ch/g"


def test_fetch_project_with_fork():
    # Use a fresh adapter so the kind cache from a previous test doesn't leak.
    a = GitLabAdapter(client=MagicMock(), instance_host="gitlab.com")
    raw = MagicMock(
        id=22, path_with_namespace="g/p", name="p",
        visibility="public",
    )
    raw.namespace = {"kind": "group", "full_path": "g"}
    raw.forked_from_project = {"path_with_namespace": "upstream/p"}
    a._client.get_project_by_path.return_value = raw
    node = a.fetch("https://gitlab.com/g/p")
    assert node.is_fork is True
    assert node.forked_from == "https://gitlab.com/upstream/p"


def test_fetch_unknown_returns_none(adapter):
    adapter._client.get_user_by_username.return_value = None
    adapter._client.get_group_by_path.return_value = None
    assert adapter.fetch("https://gitlab.epfl.ch/missing") is None


# ---------- expand ----------------------------------------------------------


def test_expand_user_emits_authored_starred_contributed():
    from unittest.mock import MagicMock
    from open_pulse_crawler.platforms.gitlab.adapter import GitLabAdapter
    from open_pulse_crawler.platforms.base import ExpandOpts
    from open_pulse_crawler.models import GitLabUserModel

    a = GitLabAdapter(client=MagicMock(), instance_host="gitlab.epfl.ch")
    user = GitLabUserModel(
        url="https://gitlab.epfl.ch/alice", login="alice",
        platform="gitlab", native_id=10,
    )
    a._client.iter_user_projects.return_value = [
        MagicMock(path_with_namespace="alice/p1"),
        MagicMock(path_with_namespace="alice/p2"),
    ]
    a._client.iter_user_starred.return_value = [
        MagicMock(path_with_namespace="other/proj"),
    ]
    a._client.iter_user_contributed.return_value = [
        MagicMock(path_with_namespace="upstream/q"),
    ]
    edges = list(a.expand(user, ExpandOpts()))
    kinds = {(e.kind, e.dst) for e in edges}
    assert ("authored", "https://gitlab.epfl.ch/alice/p1") in kinds
    assert ("authored", "https://gitlab.epfl.ch/alice/p2") in kinds
    assert ("starred", "https://gitlab.epfl.ch/other/proj") in kinds
    assert ("contributor_of", "https://gitlab.epfl.ch/upstream/q") in kinds


def test_expand_group_emits_members_subgroups_projects():
    from unittest.mock import MagicMock
    from open_pulse_crawler.platforms.gitlab.adapter import GitLabAdapter
    from open_pulse_crawler.platforms.base import ExpandOpts
    from open_pulse_crawler.models import GitLabGroupModel

    a = GitLabAdapter(client=MagicMock(), instance_host="gitlab.epfl.ch")
    grp = GitLabGroupModel(
        url="https://gitlab.epfl.ch/g", login="g", platform="gitlab", native_id=2,
    )
    a._client.iter_group_members.return_value = [
        MagicMock(username="alice"), MagicMock(username="bob"),
    ]
    a._client.iter_subgroups.return_value = [
        MagicMock(full_path="g/sub"),
    ]
    a._client.iter_group_projects.return_value = [
        MagicMock(path_with_namespace="g/p1"),
    ]
    edges = list(a.expand(grp, ExpandOpts()))
    assert any(e.kind == "member_of" and e.src == "https://gitlab.epfl.ch/alice" and e.dst == grp.url for e in edges)
    assert any(e.kind == "member_of" and e.src == "https://gitlab.epfl.ch/bob" for e in edges)
    assert any(e.kind == "subgroup_of" and e.src == "https://gitlab.epfl.ch/g/sub" and e.dst == grp.url for e in edges)
    assert any(e.kind == "authored" and e.dst == "https://gitlab.epfl.ch/g/p1" for e in edges)


def test_expand_project_contributors_with_username():
    from unittest.mock import MagicMock
    from open_pulse_crawler.platforms.gitlab.adapter import GitLabAdapter
    from open_pulse_crawler.platforms.base import ExpandOpts
    from open_pulse_crawler.models import GitLabProjectModel

    a = GitLabAdapter(client=MagicMock(), instance_host="gitlab.epfl.ch")
    proj = GitLabProjectModel(
        url="https://gitlab.epfl.ch/g/p", full_name="g/p", platform="gitlab", native_id=22,
    )
    # python-gitlab's repository_contributors returns dicts; some instances include 'username', most don't.
    a._client.iter_project_contributors.return_value = [
        {"username": "alice", "name": "Alice"},
        {"name": "Bob (no username)"},   # skipped — no stable identifier
        {"username": "carol"},
    ]
    a._client.iter_project_forks.return_value = []
    a._client.iter_project_issues.return_value = []
    a._client.iter_project_merge_requests.return_value = []
    a._client.iter_project_starrers.return_value = []
    edges = list(a.expand(proj, ExpandOpts()))
    contrib = [e for e in edges if e.kind == "contributor_of"]
    assert sorted(e.src for e in contrib) == [
        "https://gitlab.epfl.ch/alice", "https://gitlab.epfl.ch/carol",
    ]


def test_expand_project_contributor_cap():
    from unittest.mock import MagicMock
    from open_pulse_crawler.platforms.gitlab.adapter import GitLabAdapter
    from open_pulse_crawler.platforms.base import ExpandOpts
    from open_pulse_crawler.models import GitLabProjectModel

    a = GitLabAdapter(client=MagicMock(), instance_host="gitlab.epfl.ch")
    proj = GitLabProjectModel(url="https://gitlab.epfl.ch/g/p", full_name="g/p", platform="gitlab", native_id=22)
    a._client.iter_project_contributors.return_value = [
        {"username": f"u{i}"} for i in range(5)
    ]
    a._client.iter_project_forks.return_value = []
    a._client.iter_project_issues.return_value = []
    a._client.iter_project_merge_requests.return_value = []
    a._client.iter_project_starrers.return_value = []
    edges = list(a.expand(proj, ExpandOpts(max_contributors=3)))
    contrib = [e for e in edges if e.kind == "contributor_of"]
    assert len(contrib) == 3


def test_expand_project_forks():
    from unittest.mock import MagicMock
    from open_pulse_crawler.platforms.gitlab.adapter import GitLabAdapter
    from open_pulse_crawler.platforms.base import ExpandOpts
    from open_pulse_crawler.models import GitLabProjectModel

    a = GitLabAdapter(client=MagicMock(), instance_host="gitlab.epfl.ch")
    proj = GitLabProjectModel(url="https://gitlab.epfl.ch/g/p", full_name="g/p", platform="gitlab", native_id=22)
    a._client.iter_project_contributors.return_value = []
    a._client.iter_project_forks.return_value = [
        MagicMock(path_with_namespace="downstream/fork1"),
        MagicMock(path_with_namespace="downstream/fork2"),
    ]
    a._client.iter_project_issues.return_value = []
    a._client.iter_project_merge_requests.return_value = []
    a._client.iter_project_starrers.return_value = []
    edges = list(a.expand(proj, ExpandOpts()))
    fork_edges = [e for e in edges if e.kind == "forked_from"]
    assert sorted((e.src, e.dst) for e in fork_edges) == [
        ("https://gitlab.epfl.ch/downstream/fork1", proj.url),
        ("https://gitlab.epfl.ch/downstream/fork2", proj.url),
    ]


def test_expand_project_issues_gated():
    from unittest.mock import MagicMock
    from open_pulse_crawler.platforms.gitlab.adapter import GitLabAdapter
    from open_pulse_crawler.platforms.base import ExpandOpts
    from open_pulse_crawler.models import GitLabProjectModel

    a = GitLabAdapter(client=MagicMock(), instance_host="gitlab.epfl.ch")
    proj = GitLabProjectModel(url="https://gitlab.epfl.ch/g/p", full_name="g/p", platform="gitlab", native_id=22)
    a._client.iter_project_contributors.return_value = []
    a._client.iter_project_forks.return_value = []
    issue = MagicMock()
    issue.author = {"username": "alice"}
    a._client.iter_project_issues.return_value = [issue]
    a._client.iter_project_merge_requests.return_value = []
    a._client.iter_project_starrers.return_value = []

    # Without crawl_issues, the iterator must not even be called
    list(a.expand(proj, ExpandOpts(crawl_issues=False)))
    a._client.iter_project_issues.assert_not_called()

    # With crawl_issues, edges emit
    edges = list(a.expand(proj, ExpandOpts(crawl_issues=True, issue_max=10)))
    issue_edges = [e for e in edges if e.kind == "opened_issue_in"]
    assert issue_edges == [type(issue_edges[0])(src="https://gitlab.epfl.ch/alice", kind="opened_issue_in", dst=proj.url)]


def test_expand_project_prs_gated():
    from unittest.mock import MagicMock
    from open_pulse_crawler.platforms.gitlab.adapter import GitLabAdapter
    from open_pulse_crawler.platforms.base import ExpandOpts
    from open_pulse_crawler.models import GitLabProjectModel

    a = GitLabAdapter(client=MagicMock(), instance_host="gitlab.epfl.ch")
    proj = GitLabProjectModel(url="https://gitlab.epfl.ch/g/p", full_name="g/p", platform="gitlab", native_id=22)
    a._client.iter_project_contributors.return_value = []
    a._client.iter_project_forks.return_value = []
    a._client.iter_project_issues.return_value = []
    mr = MagicMock()
    mr.author = {"username": "carol"}
    a._client.iter_project_merge_requests.return_value = [mr]
    a._client.iter_project_starrers.return_value = []

    list(a.expand(proj, ExpandOpts(crawl_prs=False)))
    a._client.iter_project_merge_requests.assert_not_called()

    edges = list(a.expand(proj, ExpandOpts(crawl_prs=True, pr_max=10)))
    assert any(e.kind == "opened_pr_in" and e.src == "https://gitlab.epfl.ch/carol" and e.dst == proj.url for e in edges)


def test_expand_project_stars_gated():
    from unittest.mock import MagicMock
    from open_pulse_crawler.platforms.gitlab.adapter import GitLabAdapter
    from open_pulse_crawler.platforms.base import ExpandOpts
    from open_pulse_crawler.models import GitLabProjectModel

    a = GitLabAdapter(client=MagicMock(), instance_host="gitlab.epfl.ch")
    proj = GitLabProjectModel(url="https://gitlab.epfl.ch/g/p", full_name="g/p", platform="gitlab", native_id=22)
    a._client.iter_project_contributors.return_value = []
    a._client.iter_project_forks.return_value = []
    a._client.iter_project_issues.return_value = []
    a._client.iter_project_merge_requests.return_value = []

    # Without crawl_stars, iter_project_starrers is NOT called
    list(a.expand(proj, ExpandOpts(crawl_stars=False)))
    a._client.iter_project_starrers.assert_not_called()

    # With crawl_stars
    starrer = {"user": {"username": "dave"}}
    a._client.iter_project_starrers.return_value = [starrer]
    edges = list(a.expand(proj, ExpandOpts(crawl_stars=True)))
    assert any(e.kind == "starred" and e.src == "https://gitlab.epfl.ch/dave" and e.dst == proj.url for e in edges)


def test_expand_project_starrers_missing_endpoint_ok():
    from unittest.mock import MagicMock
    from open_pulse_crawler.platforms.gitlab.adapter import GitLabAdapter
    from open_pulse_crawler.platforms.base import ExpandOpts
    from open_pulse_crawler.models import GitLabProjectModel

    a = GitLabAdapter(client=MagicMock(), instance_host="gitlab.epfl.ch")
    proj = GitLabProjectModel(url="https://gitlab.epfl.ch/g/p", full_name="g/p", platform="gitlab", native_id=22)
    a._client.iter_project_contributors.return_value = []
    a._client.iter_project_forks.return_value = []
    a._client.iter_project_issues.return_value = []
    a._client.iter_project_merge_requests.return_value = []
    a._client.iter_project_starrers.return_value = []   # GitLabClient already swallows the 404 / AttributeError

    # Even with crawl_stars=True, the adapter must handle the empty list gracefully
    edges = list(a.expand(proj, ExpandOpts(crawl_stars=True)))
    star_edges = [e for e in edges if e.kind == "starred"]
    assert star_edges == []


# ---------- rate_limit_state ------------------------------------------------


def test_rate_limit_state_reads_client(adapter):
    adapter._client.rate_limit_remaining.return_value = 500
    adapter._client.rate_limit_limit.return_value = 2000
    adapter._client.rate_limit_reset_at.return_value = None
    rl = adapter.rate_limit_state()
    assert rl.remaining == 500
    assert rl.limit == 2000
    assert rl.reset_at is None
