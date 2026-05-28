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


# ---------- expand (Task 11 implements; Task 10 raises) ---------------------


def test_expand_raises_not_implemented(adapter):
    user = GitLabUserModel(url="https://gitlab.epfl.ch/alice", login="alice", platform="gitlab")
    with pytest.raises(NotImplementedError):
        list(adapter.expand(user, ExpandOpts()))


# ---------- rate_limit_state ------------------------------------------------


def test_rate_limit_state_reads_client(adapter):
    adapter._client.rate_limit_remaining.return_value = 500
    adapter._client.rate_limit_limit.return_value = 2000
    adapter._client.rate_limit_reset_at.return_value = None
    rl = adapter.rate_limit_state()
    assert rl.remaining == 500
    assert rl.limit == 2000
    assert rl.reset_at is None
