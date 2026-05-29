"""Tests for the Infoscience (DSpace 7) HTTP client wrapper."""
from unittest.mock import MagicMock, patch
import pytest
import httpx

from open_pulse_crawler.platforms.infoscience.client import InfoscienceClient


def test_authenticated_sets_bearer_header():
    c = InfoscienceClient(host="infoscience.epfl.ch", tokens=["dspace-tok"])
    assert c._session.headers.get("Authorization") == "Bearer dspace-tok"


def test_anonymous_sends_no_auth_header():
    c = InfoscienceClient(host="infoscience.epfl.ch", tokens=[])
    assert "Authorization" not in c._session.headers


def test_base_url_uses_https_with_host():
    c = InfoscienceClient(host="infoscience.epfl.ch", tokens=[])
    assert c.base_url == "https://infoscience.epfl.ch"


def test_rotate_in_anonymous_mode_is_noop():
    c = InfoscienceClient(host="infoscience.epfl.ch", tokens=[])
    c._rotate()  # must not raise
    assert "Authorization" not in c._session.headers


def test_rotate_cycles_token_pool():
    c = InfoscienceClient(host="infoscience.epfl.ch", tokens=["a", "b"])
    assert c._session.headers["Authorization"] == "Bearer a"
    c._rotate()
    assert c._session.headers["Authorization"] == "Bearer b"
    c._rotate()
    assert c._session.headers["Authorization"] == "Bearer a"


def _make_response(status_code: int, json_body=None, headers=None):
    r = MagicMock()
    r.status_code = status_code
    r.is_success = 200 <= status_code < 300
    r.json.return_value = json_body or {}
    r.headers = headers or {}
    if not (200 <= status_code < 300):
        r.raise_for_status.side_effect = httpx.HTTPStatusError(
            f"{status_code}", request=MagicMock(), response=r,
        )
    else:
        r.raise_for_status.return_value = None
    return r


def test_get_item_by_handle_200_returns_dict():
    c = InfoscienceClient(host="infoscience.epfl.ch", tokens=[])
    payload = {"uuid": "abc", "handle": "20.500.14299/182247", "entityType": "Publication"}
    with patch.object(c._session, "get", return_value=_make_response(200, payload)) as g:
        result = c.get_item_by_handle("20.500.14299/182247")
    g.assert_called_once_with("/server/api/pid/find", params={"id": "hdl:20.500.14299/182247"})
    assert result == payload


def test_get_item_by_handle_404_returns_none():
    c = InfoscienceClient(host="infoscience.epfl.ch", tokens=[])
    with patch.object(c._session, "get", return_value=_make_response(404)):
        assert c.get_item_by_handle("20.500.14299/missing") is None


def test_get_item_by_uuid_200():
    c = InfoscienceClient(host="infoscience.epfl.ch", tokens=[])
    payload = {"uuid": "abc", "handle": "20.500.14299/1", "entityType": "Person"}
    with patch.object(c._session, "get", return_value=_make_response(200, payload)) as g:
        result = c.get_item_by_uuid("abc")
    g.assert_called_once_with("/server/api/core/items/abc")
    assert result == payload


def test_429_retries_after_retry_after_header(monkeypatch):
    c = InfoscienceClient(host="infoscience.epfl.ch", tokens=[])
    responses = [
        _make_response(429, headers={"Retry-After": "1"}),
        _make_response(200, {"uuid": "abc"}),
    ]
    sleep_calls = []
    monkeypatch.setattr("open_pulse_crawler.platforms.infoscience.client.time.sleep",
                       lambda s: sleep_calls.append(s))
    with patch.object(c._session, "get", side_effect=responses):
        result = c.get_item_by_handle("20.500.14299/x")
    assert result == {"uuid": "abc"}
    assert sleep_calls == [1.0]


def test_429_twice_raises(monkeypatch):
    c = InfoscienceClient(host="infoscience.epfl.ch", tokens=[])
    monkeypatch.setattr("open_pulse_crawler.platforms.infoscience.client.time.sleep", lambda s: None)
    responses = [_make_response(429, headers={"Retry-After": "0"}), _make_response(429)]
    with patch.object(c._session, "get", side_effect=responses):
        with pytest.raises(httpx.HTTPStatusError):
            c.get_item_by_handle("20.500.14299/x")


def test_429_caps_retry_after_at_60s(monkeypatch):
    """A server claiming Retry-After: 9999 should still be honored only up to 60s."""
    c = InfoscienceClient(host="infoscience.epfl.ch", tokens=[])
    sleep_calls = []
    monkeypatch.setattr("open_pulse_crawler.platforms.infoscience.client.time.sleep",
                       lambda s: sleep_calls.append(s))
    responses = [_make_response(429, headers={"Retry-After": "9999"}), _make_response(200, {"uuid": "x"})]
    with patch.object(c._session, "get", side_effect=responses):
        c.get_item_by_handle("20.500.14299/x")
    assert sleep_calls == [60.0]


def test_iter_person_items_single_page():
    c = InfoscienceClient(host="infoscience.epfl.ch", tokens=[])
    body = {
        "_embedded": {
            "searchResult": {
                "_embedded": {
                    "objects": [
                        {"_embedded": {"indexableObject": {"uuid": "i1", "handle": "h/1"}}},
                        {"_embedded": {"indexableObject": {"uuid": "i2", "handle": "h/2"}}},
                    ]
                },
                "_links": {},
            }
        }
    }
    with patch.object(c._session, "get", return_value=_make_response(200, body)) as g:
        items = list(c.iter_person_items("person-uuid"))
    g.assert_called_once_with(
        "/server/api/discover/search/objects",
        params={"dsoType": "item", "query": "author.authority:person-uuid", "size": 100},
    )
    assert [i["uuid"] for i in items] == ["i1", "i2"]


def test_iter_person_items_follows_links_next():
    c = InfoscienceClient(host="infoscience.epfl.ch", tokens=[])
    page1 = {
        "_embedded": {"searchResult": {
            "_embedded": {"objects": [
                {"_embedded": {"indexableObject": {"uuid": "i1", "handle": "h/1"}}},
            ]},
            "_links": {"next": {"href": "https://infoscience.epfl.ch/server/api/discover/search/objects?dsoType=item&page=2"}},
        }},
    }
    page2 = {
        "_embedded": {"searchResult": {
            "_embedded": {"objects": [
                {"_embedded": {"indexableObject": {"uuid": "i2", "handle": "h/2"}}},
            ]},
            "_links": {},
        }},
    }
    with patch.object(c._session, "get") as g:
        g.side_effect = [_make_response(200, page1), _make_response(200, page2)]
        items = list(c.iter_person_items("person-uuid"))
    assert [i["uuid"] for i in items] == ["i1", "i2"]
    assert g.call_count == 2


def test_iter_orgunit_items_query():
    c = InfoscienceClient(host="infoscience.epfl.ch", tokens=[])
    body = {"_embedded": {"searchResult": {"_embedded": {"objects": []}, "_links": {}}}}
    with patch.object(c._session, "get", return_value=_make_response(200, body)) as g:
        list(c.iter_orgunit_items("orgunit-uuid"))
    g.assert_called_once_with(
        "/server/api/discover/search/objects",
        params={"dsoType": "item", "query": "author.parent-organization.authority:orgunit-uuid", "size": 100},
    )


def test_iter_orgunit_persons_query():
    c = InfoscienceClient(host="infoscience.epfl.ch", tokens=[])
    body = {"_embedded": {"searchResult": {"_embedded": {"objects": []}, "_links": {}}}}
    with patch.object(c._session, "get", return_value=_make_response(200, body)) as g:
        list(c.iter_orgunit_persons("orgunit-uuid"))
    g.assert_called_once_with(
        "/server/api/discover/search/objects",
        params={
            "dsoType": "item",
            "query": "dspace.entity.type:Person AND author.parent-organization.authority:orgunit-uuid",
            "size": 100,
        },
    )
