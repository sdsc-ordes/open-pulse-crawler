"""Tests for ``GitHubAdapter`` — the concrete PlatformAdapter for github.com.

The adapter is the thin seam between the BFS engine and ``GitHubClient``:
- ``classify`` shape-matches a URL to a ``NodeKind`` (or returns ``None``);
- ``normalize_uri`` delegates to ``node_id.canonical_url``;
- ``fetch`` returns whatever the underlying client returned, unchanged;
- ``expand`` is the only place that converts model shorthand (logins /
  ``owner/repo`` strings) to canonical URLs at the edge boundary;
- ``rate_limit_state`` reads the client's flat rate-limit attributes.

Tests mock ``GitHubClient`` so they don't need network access.
"""
from unittest.mock import MagicMock

import pytest

from open_pulse_crawler.models import OrgModel, RepoModel, TeamModel, UserModel
from open_pulse_crawler.node_id import NodeKind
from open_pulse_crawler.platforms.base import ExpandOpts
from open_pulse_crawler.platforms.github.adapter import GitHubAdapter


@pytest.fixture
def adapter():
    return GitHubAdapter(client=MagicMock(), instance_host="github.com")


# ---------- class-var / identity --------------------------------------------


def test_platform_class_var(adapter):
    assert GitHubAdapter.platform == "github"
    assert adapter.instance_host == "github.com"


# ---------- classify --------------------------------------------------------


def test_classify_user_or_org(adapter):
    assert adapter.classify("https://github.com/torvalds") == NodeKind.USER_OR_ORG


def test_classify_repo(adapter):
    assert adapter.classify("https://github.com/owner/repo") == NodeKind.REPO


def test_classify_team(adapter):
    assert (
        adapter.classify("https://github.com/orgs/anthropic/teams/core")
        == NodeKind.TEAM
    )


def test_classify_unknown_returns_none(adapter):
    # Reserved path prefixes and paths that don't match any known shape are
    # classified as unknown so the crawler can skip them gracefully.
    assert adapter.classify("https://github.com/orgs") is None
    assert adapter.classify("https://github.com/") is None


# ---------- normalize_uri ---------------------------------------------------


def test_normalize_strips_trailing_slash(adapter):
    # canonical_url strips a trailing slash but preserves path casing.
    assert (
        adapter.normalize_uri("https://github.com/Torvalds/")
        == "https://github.com/Torvalds"
    )


# ---------- fetch -----------------------------------------------------------


def test_fetch_user_routes_to_get_user(adapter):
    user = UserModel(url="https://github.com/torvalds", login="torvalds")
    adapter._client.get_user.return_value = user
    node = adapter.fetch("https://github.com/torvalds")
    assert node is user  # adapter returns what the client gave us, unchanged
    adapter._client.get_user.assert_called_once()


def test_fetch_repo_routes_to_get_repository(adapter):
    repo = RepoModel(url="https://github.com/o/r", full_name="o/r")
    adapter._client.get_repository.return_value = repo
    node = adapter.fetch("https://github.com/o/r")
    assert node is repo


def test_fetch_team_returns_none_when_client_lacks_get_team(adapter):
    # GitHubClient on develop has no get_team; the adapter must degrade
    # cleanly rather than raise AttributeError.
    if hasattr(adapter._client, "get_team"):
        # The MagicMock auto-creates attributes; explicitly remove so we
        # simulate the real client's surface.
        del adapter._client.get_team
    node = adapter.fetch("https://github.com/orgs/anthropic/teams/core")
    assert node is None


def test_fetch_team_routes_when_client_has_get_team(adapter):
    # When Task 6 adds GitHubClient.get_team, the adapter should route to it.
    from open_pulse_crawler.models import TeamModel
    team = TeamModel(
        url="https://github.com/orgs/anthropic/teams/core",
        full_name="anthropic/core", org="anthropic", slug="core",
    )
    adapter._client.get_team.return_value = team
    node = adapter.fetch("https://github.com/orgs/anthropic/teams/core")
    assert node is team
    adapter._client.get_team.assert_called_once()


# ---------- expand: UserModel -----------------------------------------------


def test_expand_user_emits_follower_following_starred_watched_authored_forked(adapter):
    user = UserModel(
        url="https://github.com/a",
        login="a",
        followers=["b", "c"],
        following=["d"],
        starred_repositories=["x/y"],
        watched_repositories=["m/n"],
        authored_repositories=["a/r1"],
        forked_repositories=["a/r2"],
    )
    edges = list(adapter.expand(user, ExpandOpts()))

    follower_edges = [(e.src, e.dst) for e in edges if e.kind == "follower_of"]
    # followers: each follower follows `a` (src=follower, dst=a)
    assert ("https://github.com/b", "https://github.com/a") in follower_edges
    assert ("https://github.com/c", "https://github.com/a") in follower_edges
    # following: `a` follows each one (src=a, dst=followed)
    assert ("https://github.com/a", "https://github.com/d") in follower_edges

    assert any(
        e.kind == "starred" and e.dst == "https://github.com/x/y" for e in edges
    )
    assert any(
        e.kind == "watched" and e.dst == "https://github.com/m/n" for e in edges
    )
    assert any(
        e.kind == "authored" and e.dst == "https://github.com/a/r1" for e in edges
    )
    assert any(
        e.kind == "forked" and e.dst == "https://github.com/a/r2" for e in edges
    )


# ---------- expand: OrgModel ------------------------------------------------


def test_expand_org_emits_member_authored_forked(adapter):
    org = OrgModel(
        url="https://github.com/o",
        login="o",
        members=["x", "y"],
        authored_repositories=["o/r"],
        forked_repositories=["o/f"],
    )
    edges = list(adapter.expand(org, ExpandOpts()))
    assert any(
        e.kind == "member_of"
        and e.src == "https://github.com/x"
        and e.dst == "https://github.com/o"
        for e in edges
    )
    assert any(
        e.kind == "authored" and e.dst == "https://github.com/o/r" for e in edges
    )
    assert any(
        e.kind == "forked" and e.dst == "https://github.com/o/f" for e in edges
    )


# ---------- expand: RepoModel -----------------------------------------------


def test_expand_repo_emits_contributors_and_forked_from(adapter):
    repo = RepoModel(
        url="https://github.com/o/r",
        full_name="o/r",
        contributors=["alice", "bob"],
        is_fork=True,
        forked_from="upstream/r",
    )
    edges = list(adapter.expand(repo, ExpandOpts()))
    assert any(
        e.kind == "contributor_of" and e.src == "https://github.com/alice"
        for e in edges
    )
    assert any(
        e.kind == "contributor_of" and e.src == "https://github.com/bob"
        for e in edges
    )
    assert any(
        e.kind == "forked_from" and e.dst == "https://github.com/upstream/r"
        for e in edges
    )


def test_expand_repo_max_contributors_cap(adapter):
    repo = RepoModel(
        url="https://github.com/o/r",
        full_name="o/r",
        contributors=["a", "b", "c", "d", "e"],
    )
    edges = [
        e
        for e in adapter.expand(repo, ExpandOpts(max_contributors=2))
        if e.kind == "contributor_of"
    ]
    assert len(edges) == 2


def test_expand_repo_issues_gated_by_opts(adapter):
    repo = RepoModel(
        url="https://github.com/o/r",
        full_name="o/r",
        issue_authors=["alice"],
    )
    no_issue = [
        e
        for e in adapter.expand(repo, ExpandOpts(crawl_issues=False))
        if e.kind == "opened_issue_in"
    ]
    assert no_issue == []
    with_issue = [
        e
        for e in adapter.expand(repo, ExpandOpts(crawl_issues=True))
        if e.kind == "opened_issue_in"
    ]
    assert with_issue and with_issue[0].src == "https://github.com/alice"


def test_expand_repo_prs_gated_by_opts(adapter):
    repo = RepoModel(
        url="https://github.com/o/r",
        full_name="o/r",
        pr_authors=["alice"],
        pr_reviewers=["bob"],
        commenters=["c"],
    )
    no_prs = [
        e
        for e in adapter.expand(repo, ExpandOpts(crawl_prs=False))
        if e.kind in ("opened_pr_in", "reviewed_pr_in", "commented_in")
    ]
    assert no_prs == []
    with_prs = list(adapter.expand(repo, ExpandOpts(crawl_prs=True)))
    assert any(
        e.kind == "opened_pr_in" and e.src == "https://github.com/alice"
        for e in with_prs
    )
    assert any(
        e.kind == "reviewed_pr_in" and e.src == "https://github.com/bob"
        for e in with_prs
    )
    assert any(
        e.kind == "commented_in" and e.src == "https://github.com/c"
        for e in with_prs
    )


def test_expand_repo_dependencies_gated(adapter):
    repo = RepoModel(
        url="https://github.com/o/r",
        full_name="o/r",
        dependencies=["dep/a"],
        dependents=["dep/b"],
    )
    none_dep = [
        e for e in adapter.expand(repo, ExpandOpts()) if e.kind == "depends_on"
    ]
    assert none_dep == []
    out = list(
        adapter.expand(
            repo, ExpandOpts(crawl_dependencies=True, crawl_dependents=True)
        )
    )
    # repo `o/r` depends_on `dep/a` (out-edge of the repo).
    assert any(
        e.kind == "depends_on"
        and e.src == "https://github.com/o/r"
        and e.dst == "https://github.com/dep/a"
        for e in out
    )
    # `dep/b` depends_on `o/r` (dependents are reversed).
    assert any(
        e.kind == "depends_on"
        and e.src == "https://github.com/dep/b"
        and e.dst == "https://github.com/o/r"
        for e in out
    )


# ---------- expand: TeamModel -----------------------------------------------


def test_expand_team_emits_member_and_repo_edges(adapter):
    team = TeamModel(
        url="https://github.com/orgs/anthropic/teams/core",
        full_name="anthropic/core",
        org="anthropic",
        slug="core",
        members=["alice"],
        repositories=["anthropic/foo"],
    )
    edges = list(adapter.expand(team, ExpandOpts()))
    assert any(
        e.kind == "member_of"
        and e.src == "https://github.com/alice"
        and e.dst == team.url
        for e in edges
    )
    assert any(
        e.kind == "repo_of"
        and e.src == "https://github.com/anthropic/foo"
        and e.dst == team.url
        for e in edges
    )


# ---------- rate_limit_state ------------------------------------------------


def test_rate_limit_state_reads_client_state(adapter):
    # The adapter reads the client's flat ``rate_limit_remaining`` /
    # ``rate_limit_limit`` attributes. The real client exposes these as
    # @property accessors over its multi-token state; the mock supplies them
    # directly here.
    adapter._client.rate_limit_remaining = 4321
    adapter._client.rate_limit_limit = 5000
    rl = adapter.rate_limit_state()
    assert rl.remaining == 4321 and rl.limit == 5000
