"""Unit tests for the thin python-gitlab wrapper used by `GitLabAdapter`.

The wrapper is intentionally small — it owns token rotation, 404→None
mapping on path lookups, and graceful fallback for endpoints that some
self-hosted GitLab versions don't expose (e.g. `/starrers`). Network calls
are mocked via `_gl_factory`, so these tests run offline.
"""

from unittest.mock import MagicMock

import gitlab
import pytest

from open_pulse_crawler.platforms.gitlab.client import GitLabClient


def make_client(gl_mock=None, tokens=None):
    """Build a `GitLabClient` whose underlying `gitlab.Gitlab` is `gl_mock`."""
    return GitLabClient(
        host="gitlab.example.com",
        tokens=tokens or ["t1"],
        _gl_factory=lambda *a, **kw: gl_mock if gl_mock is not None else MagicMock(),
    )


def test_requires_tokens():
    with pytest.raises(ValueError):
        GitLabClient(host="gitlab.example.com", tokens=[])


def test_init_builds_gitlab_instance_with_current_token():
    factory = MagicMock()
    GitLabClient(host="gitlab.example.com", tokens=["t1"], _gl_factory=factory)
    factory.assert_called_once()
    args, kwargs = factory.call_args
    assert kwargs.get("private_token") == "t1"
    # URL should be the https://host form
    if args:
        assert args[0] == "https://gitlab.example.com"
    else:
        # python-gitlab also accepts url= kwarg
        assert kwargs.get("url") == "https://gitlab.example.com"


def test_rotate_cycles_to_next_token():
    factory = MagicMock()
    c = GitLabClient(host="gitlab.example.com", tokens=["t1", "t2"], _gl_factory=factory)
    factory.reset_mock()
    c._rotate()
    factory.assert_called_once()
    _, kwargs = factory.call_args
    assert kwargs.get("private_token") == "t2"


def test_get_user_by_username_hit():
    gl = MagicMock()
    user = MagicMock(id=42, username="alice")
    gl.users.list.return_value = [user]
    c = make_client(gl)
    assert c.get_user_by_username("alice") is user


def test_get_user_by_username_miss():
    gl = MagicMock()
    gl.users.list.return_value = []
    assert make_client(gl).get_user_by_username("nobody") is None


def test_get_group_by_path_hit():
    gl = MagicMock()
    grp = MagicMock(id=99, full_path="g/sub")
    gl.groups.get.return_value = grp
    assert make_client(gl).get_group_by_path("g/sub") is grp


def test_get_group_by_path_404_returns_none():
    gl = MagicMock()
    gl.groups.get.side_effect = gitlab.GitlabGetError(response_code=404)
    assert make_client(gl).get_group_by_path("g/missing") is None


def test_get_group_by_path_propagates_non_404():
    gl = MagicMock()
    gl.groups.get.side_effect = gitlab.GitlabGetError(response_code=500)
    with pytest.raises(gitlab.GitlabGetError):
        make_client(gl).get_group_by_path("g/x")


def test_get_project_by_path_hit():
    gl = MagicMock()
    proj = MagicMock(id=22, path_with_namespace="g/p")
    gl.projects.get.return_value = proj
    assert make_client(gl).get_project_by_path("g/p") is proj


def test_get_project_by_path_404_returns_none():
    gl = MagicMock()
    gl.projects.get.side_effect = gitlab.GitlabGetError(response_code=404)
    assert make_client(gl).get_project_by_path("g/missing") is None


def test_iter_group_projects_calls_python_gitlab():
    gl = MagicMock()
    projects = [MagicMock(path_with_namespace="g/p1"), MagicMock(path_with_namespace="g/p2")]
    gl.groups.get.return_value.projects.list.return_value = projects
    c = make_client(gl)
    out = c.iter_group_projects(group_id=2)
    assert out == projects
    gl.groups.get.assert_called_once_with(2)


def test_iter_project_starrers_missing_endpoint_returns_empty():
    gl = MagicMock()
    proj_with_no_starrers = MagicMock(spec=[])  # spec=[] means no attribute named 'starrers'
    gl.projects.get.return_value = proj_with_no_starrers
    c = make_client(gl)
    assert c.iter_project_starrers(project_id=22) == []


def test_iter_project_starrers_raises_gitlaberror_returns_empty():
    gl = MagicMock()
    proj = gl.projects.get.return_value
    proj.starrers.list.side_effect = gitlab.GitlabGetError(response_code=404)
    c = make_client(gl)
    assert c.iter_project_starrers(project_id=22) == []


def test_iter_project_issues_uses_per_page_cap():
    gl = MagicMock()
    issues_mgr = gl.projects.get.return_value.issues
    issues_mgr.list.return_value = ["issue1", "issue2"]
    c = make_client(gl)
    c.iter_project_issues(project_id=22, max_n=50)
    issues_mgr.list.assert_called_once()
    kwargs = issues_mgr.list.call_args.kwargs
    assert kwargs.get("per_page") == 50
    assert kwargs.get("get_all") is False
