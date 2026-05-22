"""Unit tests for GitHubGraphQLClient (mocked httpx)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from open_pulse_crawler.graphql_client import GitHubGraphQLClient


def _gql_response(data: dict, status_code: int = 200):
    """Build a httpx-like response mock returning the given GraphQL body."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = ""
    body = {"data": data, "errors": None}
    # rateLimit defaults if the caller did not include one.
    if data and "rateLimit" not in data:
        body["data"]["rateLimit"] = {
            "cost": 1, "remaining": 4999, "resetAt": "2999-01-01T00:00:00Z"
        }
    resp.json.return_value = body
    return resp


def _rest_response(payload, status_code: int = 200):
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = ""
    resp.json.return_value = payload
    return resp


@pytest.fixture()
def gql_client():
    return GitHubGraphQLClient(tokens=["fake-token"])


def test_get_user_shape_matches_cached_dict(gql_client):
    user_payload = {
        "user": {
            "login": "alice",
            "name": "Alice A.",
            "databaseId": 42,
            "followers": {"nodes": [{"login": "bob"}, {"login": "carol"}]},
            "following": {"nodes": [{"login": "bob"}]},
            "starredRepositories": {"nodes": [{"nameWithOwner": "x/a"}, {"nameWithOwner": "x/b"}]},
            "watching": {"nodes": [{"nameWithOwner": "x/a"}]},
            "organizations": {"nodes": [{"login": "wonderland"}]},
            "repositories": {"nodes": [
                {"nameWithOwner": "alice/repo1", "isFork": False},
                {"nameWithOwner": "alice/repo2", "isFork": True},
            ]},
        }
    }

    with patch.object(gql_client._http, "post", return_value=_gql_response(user_payload)):
        out = gql_client.get_user("alice")

    assert out == {
        "login": "alice",
        "name": "Alice A.",
        "id": 42,
        "type": "User",
        "repos": [
            {"full_name": "alice/repo1", "fork": False},
            {"full_name": "alice/repo2", "fork": True},
        ],
        "orgs": ["wonderland"],
        "followers": ["bob", "carol"],
        "following": ["bob"],
        "starred": ["x/a", "x/b"],
        "watching": ["x/a"],
    }
    assert gql_client.stats["graphql_calls"] == 1
    assert gql_client.stats["graphql_points"] == 1


def test_get_user_returns_none_when_login_is_an_org(gql_client):
    # GraphQL `user(login: ...)` returns null when the login is actually an org.
    with patch.object(gql_client._http, "post", return_value=_gql_response({"user": None})):
        assert gql_client.get_user("acme") is None


def test_get_organization_includes_teams(gql_client):
    org_payload = {
        "organization": {
            "login": "acme",
            "name": "Acme Co",
            "databaseId": 1,
            "membersWithRole": {"nodes": [{"login": "alice"}, {"login": "bob"}]},
            "repositories": {"nodes": [
                {"nameWithOwner": "acme/widget", "isFork": False},
            ]},
            "teams": {"nodes": [
                {
                    "slug": "core",
                    "name": "Core",
                    "description": "Core team",
                    "privacy": "CLOSED",
                    "databaseId": 100,
                    "parentTeam": None,
                    "members": {"nodes": [{"login": "alice"}]},
                    "repositories": {"nodes": [{"nameWithOwner": "acme/widget"}]},
                },
                {
                    "slug": "infra",
                    "name": "Infra",
                    "description": "",
                    "privacy": "SECRET",
                    "databaseId": 101,
                    "parentTeam": {"slug": "core"},
                    "members": {"nodes": []},
                    "repositories": {"nodes": []},
                },
            ]},
        }
    }

    with patch.object(gql_client._http, "post", return_value=_gql_response(org_payload)):
        out = gql_client.get_organization("acme")

    assert out["login"] == "acme"
    assert out["type"] == "Organization"
    assert out["members"] == ["alice", "bob"]
    assert out["repos"] == [{"full_name": "acme/widget", "fork": False}]
    assert len(out["teams"]) == 2
    core = next(t for t in out["teams"] if t["slug"] == "core")
    assert core["members"] == ["alice"]
    assert core["repositories"] == ["acme/widget"]
    assert core["parent_slug"] is None
    infra = next(t for t in out["teams"] if t["slug"] == "infra")
    assert infra["parent_slug"] == "core"


def test_get_repository_with_issues_and_prs(gql_client):
    gql_client.crawl_issues = True
    gql_client.crawl_prs = True
    gql_client.issue_max = 5
    gql_client.pr_max = 5

    repo_payload = {
        "repository": {
            "nameWithOwner": "org/repo",
            "name": "repo",
            "databaseId": 7,
            "isFork": False,
            "parent": None,
            "owner": {"__typename": "Organization", "login": "org"},
            "issues": {"nodes": [
                {
                    "author": {"login": "alice"},
                    "comments": {"nodes": [{"author": {"login": "bob"}}]},
                },
                {
                    "author": {"login": "bob"},
                    "comments": {"nodes": [{"author": None}]},  # deleted commenter
                },
            ]},
            "pullRequests": {"nodes": [
                {
                    "author": {"login": "carol"},
                    "comments": {"nodes": [{"author": {"login": "dan"}}]},
                    "reviews": {"nodes": [{"author": {"login": "alice"}}]},
                },
            ]},
        }
    }

    with patch.object(gql_client._http, "post", return_value=_gql_response(repo_payload)), \
         patch.object(gql_client._http, "get", return_value=_rest_response([
             {"login": "alice"}, {"login": "bob"}, {"login": None},
         ])):
        out = gql_client.get_repository("org/repo")

    assert out["full_name"] == "org/repo"
    assert out["owner"] == "org"
    assert out["owner_type"] == "Organization"
    assert out["is_fork"] is False
    assert out["parent"] is None
    assert out["contributors"] == ["alice", "bob"]  # None filtered out
    assert out["issue_authors"] == ["alice", "bob"]
    assert out["pr_authors"] == ["carol"]
    # commenters: issue commenters first, then PR commenters
    assert out["commenters"] == ["bob", "dan"]
    assert out["pr_reviewers"] == ["alice"]


def test_get_repository_skips_issue_pr_fetch_when_flags_off(gql_client):
    """When flags are off, issues/prs are excluded from the query and lists stay empty."""
    repo_payload = {
        "repository": {
            "nameWithOwner": "org/repo",
            "name": "repo",
            "databaseId": 7,
            "isFork": False,
            "parent": None,
            "owner": {"__typename": "User", "login": "alice"},
            # issues/pullRequests omitted via @include(if: false)
        }
    }

    with patch.object(gql_client._http, "post", return_value=_gql_response(repo_payload)), \
         patch.object(gql_client._http, "get", return_value=_rest_response([])):
        out = gql_client.get_repository("alice/repo")

    assert out["issue_authors"] == []
    assert out["pr_authors"] == []
    assert out["commenters"] == []
    assert out["pr_reviewers"] == []


def test_get_repository_returns_none_for_missing_repo(gql_client):
    with patch.object(gql_client._http, "post", return_value=_gql_response({"repository": None})):
        assert gql_client.get_repository("ghost/repo") is None


def test_get_user_paginates_starred(gql_client):
    """When starred has hasNextPage, follow-up queries collect additional pages."""
    page1 = {
        "user": {
            "login": "alice",
            "name": "",
            "databaseId": 1,
            "followers": {"nodes": []},
            "following": {"nodes": []},
            "starredRepositories": {
                "nodes": [{"nameWithOwner": f"x/r{i}"} for i in range(5)],
                "pageInfo": {"hasNextPage": True, "endCursor": "c1"},
            },
            "watching": {"nodes": []},
            "organizations": {"nodes": []},
            "repositories": {
                "nodes": [],
                "pageInfo": {"hasNextPage": False, "endCursor": None},
            },
        }
    }
    page2 = {
        "user": {
            "starredRepositories": {
                "nodes": [{"nameWithOwner": "x/r5"}, {"nameWithOwner": "x/r6"}],
                "pageInfo": {"hasNextPage": False, "endCursor": "c2"},
            }
        }
    }

    with patch.object(gql_client._http, "post", side_effect=[
        _gql_response(page1),
        _gql_response(page2),
    ]):
        out = gql_client.get_user("alice")

    assert out["starred"] == [f"x/r{i}" for i in range(7)]


def test_get_user_paginates_followers_and_following(gql_client):
    """followers and following follow their pageInfo cursors to the full list."""
    page1 = {
        "user": {
            "login": "alice",
            "name": "",
            "databaseId": 1,
            "followers": {
                "nodes": [{"login": f"f{i}"} for i in range(3)],
                "pageInfo": {"hasNextPage": True, "endCursor": "fc1"},
            },
            "following": {
                "nodes": [{"login": "g0"}],
                "pageInfo": {"hasNextPage": True, "endCursor": "gc1"},
            },
            "starredRepositories": {
                "nodes": [],
                "pageInfo": {"hasNextPage": False, "endCursor": None},
            },
            "watching": {
                "nodes": [],
                "pageInfo": {"hasNextPage": False, "endCursor": None},
            },
            "organizations": {"nodes": []},
            "repositories": {
                "nodes": [],
                "pageInfo": {"hasNextPage": False, "endCursor": None},
            },
        }
    }
    followers_page2 = {
        "user": {
            "followers": {
                "nodes": [{"login": "f3"}],
                "pageInfo": {"hasNextPage": False, "endCursor": "fc2"},
            }
        }
    }
    following_page2 = {
        "user": {
            "following": {
                "nodes": [{"login": "g1"}, {"login": "g2"}],
                "pageInfo": {"hasNextPage": False, "endCursor": "gc2"},
            }
        }
    }

    # Order: main query, then followers follow-up, then following follow-up.
    with patch.object(gql_client._http, "post", side_effect=[
        _gql_response(page1),
        _gql_response(followers_page2),
        _gql_response(following_page2),
    ]):
        out = gql_client.get_user("alice")

    assert out["followers"] == ["f0", "f1", "f2", "f3"]
    assert out["following"] == ["g0", "g1", "g2"]


def test_get_user_paginates_repos(gql_client):
    """Repositories with hasNextPage follow up via _USER_REPOS_PAGE."""
    page1 = {
        "user": {
            "login": "bob",
            "name": "",
            "databaseId": 2,
            "followers": {"nodes": []},
            "following": {"nodes": []},
            "starredRepositories": {
                "nodes": [],
                "pageInfo": {"hasNextPage": False, "endCursor": None},
            },
            "watching": {"nodes": []},
            "organizations": {"nodes": []},
            "repositories": {
                "nodes": [
                    {"nameWithOwner": "bob/r1", "isFork": False},
                    {"nameWithOwner": "bob/r2", "isFork": True},
                ],
                "pageInfo": {"hasNextPage": True, "endCursor": "rc1"},
            },
        }
    }
    page2 = {
        "user": {
            "repositories": {
                "nodes": [{"nameWithOwner": "bob/r3", "isFork": False}],
                "pageInfo": {"hasNextPage": False, "endCursor": None},
            }
        }
    }

    with patch.object(gql_client._http, "post", side_effect=[
        _gql_response(page1),
        _gql_response(page2),
    ]):
        out = gql_client.get_user("bob")

    assert [r["full_name"] for r in out["repos"]] == ["bob/r1", "bob/r2", "bob/r3"]
    assert out["repos"][1]["fork"] is True


def test_get_user_falls_back_to_rest_for_orgs_when_scope_missing(gql_client):
    """If the GraphQL query is rejected for INSUFFICIENT_SCOPES, retry the
    no-orgs variant and re-fetch orgs via REST."""
    # First call: scope error => data null
    err_resp = MagicMock()
    err_resp.status_code = 200
    err_resp.text = ""
    err_resp.json.return_value = {
        "data": None,
        "errors": [{"type": "INSUFFICIENT_SCOPES", "message": "needs read:org"}],
    }

    # Second call: no-orgs query succeeds
    page2 = {
        "user": {
            "login": "carol",
            "name": "Carol",
            "databaseId": 3,
            "followers": {"nodes": []},
            "following": {"nodes": []},
            "starredRepositories": {
                "nodes": [],
                "pageInfo": {"hasNextPage": False, "endCursor": None},
            },
            "watching": {"nodes": []},
            "repositories": {
                "nodes": [],
                "pageInfo": {"hasNextPage": False, "endCursor": None},
            },
        }
    }

    rest_orgs = _rest_response([{"login": "wonderland"}, {"login": "looking-glass"}])

    with patch.object(gql_client._http, "post", side_effect=[err_resp, _gql_response(page2)]), \
         patch.object(gql_client._http, "get", return_value=rest_orgs):
        out = gql_client.get_user("carol")

    assert out["login"] == "carol"
    assert out["orgs"] == ["wonderland", "looking-glass"]


def test_get_user_pagination_respects_max_per_list():
    """Cap on max_per_list stops pagination even when hasNextPage is True."""
    client = GitHubGraphQLClient(tokens=["t"], max_per_list=3)
    page1 = {
        "user": {
            "login": "x",
            "name": "",
            "databaseId": 1,
            "followers": {"nodes": []},
            "following": {"nodes": []},
            "starredRepositories": {
                "nodes": [{"nameWithOwner": "a/0"}, {"nameWithOwner": "a/1"}],
                "pageInfo": {"hasNextPage": True, "endCursor": "c1"},
            },
            "watching": {"nodes": []},
            "organizations": {"nodes": []},
            "repositories": {
                "nodes": [],
                "pageInfo": {"hasNextPage": False, "endCursor": None},
            },
        }
    }
    page2 = {
        "user": {
            "starredRepositories": {
                "nodes": [{"nameWithOwner": "a/2"}, {"nameWithOwner": "a/3"}],
                "pageInfo": {"hasNextPage": True, "endCursor": "c2"},
            }
        }
    }
    # Should stop after page2 because count >= 3; page3 must NOT be called.
    with patch.object(client._http, "post", side_effect=[_gql_response(page1), _gql_response(page2)]):
        out = client.get_user("x")

    assert out["starred"] == ["a/0", "a/1", "a/2", "a/3"]
    # If we tried page 3 a StopIteration would fire — its absence is the assertion.


def test_get_stats_reports_points(gql_client):
    gql_client.stats["graphql_calls"] = 3
    gql_client.stats["graphql_points"] = 7
    gql_client.stats["cache_hits"] = 2
    stats = gql_client.get_stats()
    assert stats["graphql_calls"] == 3
    assert stats["graphql_points"] == 7
    assert stats["efficiency"]["points_per_call"] == pytest.approx(7 / 3)
    # Field names that the CLI table reads.
    assert "api_calls" in stats
