"""Unit tests for the thin httpx wrapper used by the Zenodo adapter.

The wrapper owns construction (anonymous vs. Bearer auth), token rotation,
and the per-host disk-cache hookup. Single-entity fetches (Task 4) and
iterators (Task 5) build on top of that.
"""

from unittest.mock import MagicMock, patch
import httpx
import pytest

from open_pulse_crawler.platforms.zenodo.client import ZenodoClient


def test_authenticated_sets_bearer_header():
    c = ZenodoClient(host="zenodo.org", tokens=["zen-pat-abc"])
    assert c._session.headers.get("Authorization") == "Bearer zen-pat-abc"


def test_anonymous_sends_no_auth_header():
    c = ZenodoClient(host="zenodo.org", tokens=[])
    assert "Authorization" not in c._session.headers


def test_base_url_uses_https_with_host():
    c = ZenodoClient(host="sandbox.zenodo.org", tokens=[])
    assert c.base_url == "https://sandbox.zenodo.org"


def test_rotate_in_anonymous_mode_is_noop():
    c = ZenodoClient(host="zenodo.org", tokens=[])
    c._rotate()  # must not raise
    assert "Authorization" not in c._session.headers


def test_rotate_cycles_token_pool():
    c = ZenodoClient(host="zenodo.org", tokens=["a", "b", "c"])
    assert c._session.headers["Authorization"] == "Bearer a"
    c._rotate()
    assert c._session.headers["Authorization"] == "Bearer b"
    c._rotate()
    assert c._session.headers["Authorization"] == "Bearer c"
    c._rotate()
    assert c._session.headers["Authorization"] == "Bearer a"  # wraps


# ---- single-entity fetches (Task 4) ---------------------------------------


def _make_response(status_code: int, json_body=None):
    r = MagicMock()
    r.status_code = status_code
    r.is_success = 200 <= status_code < 300
    r.json.return_value = json_body or {}
    # raise_for_status: real httpx raises HTTPStatusError for non-2xx
    if not (200 <= status_code < 300):
        r.raise_for_status.side_effect = httpx.HTTPStatusError(
            f"{status_code}", request=MagicMock(), response=r,
        )
    else:
        r.raise_for_status.return_value = None
    return r


def test_get_record_200_returns_dict():
    c = ZenodoClient(host="zenodo.org", tokens=[])
    payload = {"id": 7234562, "doi": "10.5281/zenodo.7234562"}
    with patch.object(c._session, "get", return_value=_make_response(200, payload)) as g:
        result = c.get_record(7234562)
    g.assert_called_once_with("/api/records/7234562")
    assert result == payload


def test_get_record_404_returns_none():
    c = ZenodoClient(host="zenodo.org", tokens=[])
    with patch.object(c._session, "get", return_value=_make_response(404)):
        assert c.get_record(99999999) is None


def test_get_record_500_raises():
    c = ZenodoClient(host="zenodo.org", tokens=[])
    with patch.object(c._session, "get", return_value=_make_response(500)):
        with pytest.raises(httpx.HTTPStatusError):
            c.get_record(7234562)


def test_get_community_path_and_parse():
    c = ZenodoClient(host="zenodo.org", tokens=[])
    payload = {"id": "sdsc-ordes", "metadata": {"title": "SDSC"}}
    with patch.object(c._session, "get", return_value=_make_response(200, payload)) as g:
        result = c.get_community("sdsc-ordes")
    g.assert_called_once_with("/api/communities/sdsc-ordes")
    assert result == payload


def test_get_community_404_returns_none():
    c = ZenodoClient(host="zenodo.org", tokens=[])
    with patch.object(c._session, "get", return_value=_make_response(404)):
        assert c.get_community("does-not-exist") is None


def test_get_user_path_and_parse():
    c = ZenodoClient(host="zenodo.org", tokens=[])
    payload = {"id": 12345, "username": "alice"}
    with patch.object(c._session, "get", return_value=_make_response(200, payload)) as g:
        result = c.get_user(12345)
    g.assert_called_once_with("/api/users/12345")
    assert result == payload


def test_get_user_401_returns_none_and_logs():
    """When /api/users/<id> requires auth (common anonymously), degrade to None."""
    c = ZenodoClient(host="zenodo.org", tokens=[])
    with patch.object(c._session, "get", return_value=_make_response(401)):
        assert c.get_user(12345) is None


def test_get_user_403_returns_none():
    c = ZenodoClient(host="zenodo.org", tokens=[])
    with patch.object(c._session, "get", return_value=_make_response(403)):
        assert c.get_user(12345) is None
