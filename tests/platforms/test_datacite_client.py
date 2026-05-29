"""Tests for the DataCite HTTP client wrapper."""
from unittest.mock import MagicMock, patch
import pytest
import httpx

from open_pulse_crawler.platforms.datacite_adapter.client import DataCiteHTTPClient


def test_authenticated_sets_bearer_header():
    c = DataCiteHTTPClient(host="api.datacite.org", tokens=["dc-tok"])
    assert c._session.headers.get("Authorization") == "Bearer dc-tok"


def test_anonymous_sends_no_auth_header():
    c = DataCiteHTTPClient(host="api.datacite.org", tokens=[])
    assert "Authorization" not in c._session.headers


def test_base_url_uses_https_with_host():
    c = DataCiteHTTPClient(host="api.datacite.org", tokens=[])
    assert c.base_url == "https://api.datacite.org"


def test_session_accepts_jsonapi_content_type():
    c = DataCiteHTTPClient(host="api.datacite.org", tokens=[])
    assert c._session.headers["Accept"] == "application/vnd.api+json"


def test_rotate_in_anonymous_mode_is_noop():
    c = DataCiteHTTPClient(host="api.datacite.org", tokens=[])
    c._rotate()
    assert "Authorization" not in c._session.headers


def test_rotate_cycles_token_pool():
    c = DataCiteHTTPClient(host="api.datacite.org", tokens=["a", "b"])
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


def test_get_doi_200_returns_data_dict():
    c = DataCiteHTTPClient(host="api.datacite.org", tokens=[])
    payload = {"data": {"id": "10.5281/zenodo.42",
                        "attributes": {"titles": [{"title": "x"}]}}}
    with patch.object(c._session, "get", return_value=_make_response(200, payload)) as g:
        result = c.get_doi("10.5281/zenodo.42")
    g.assert_called_once_with("/dois/10.5281/zenodo.42")
    assert result == payload["data"]


def test_get_doi_404_returns_none():
    c = DataCiteHTTPClient(host="api.datacite.org", tokens=[])
    with patch.object(c._session, "get", return_value=_make_response(404)):
        assert c.get_doi("10.1038/missing") is None


def test_get_client_200_returns_data_dict():
    c = DataCiteHTTPClient(host="api.datacite.org", tokens=[])
    payload = {"data": {"id": "cern.zenodo", "attributes": {"name": "Zenodo"}}}
    with patch.object(c._session, "get", return_value=_make_response(200, payload)) as g:
        result = c.get_client("cern.zenodo")
    g.assert_called_once_with("/clients/cern.zenodo")
    assert result == payload["data"]


def test_get_client_prefixes_200_returns_list():
    c = DataCiteHTTPClient(host="api.datacite.org", tokens=[])
    payload = {"data": [{"id": "10.5281"}, {"id": "10.5072"}]}
    with patch.object(c._session, "get", return_value=_make_response(200, payload)) as g:
        result = c.get_client_prefixes("cern.zenodo")
    g.assert_called_once_with("/clients/cern.zenodo/relationships/prefixes")
    assert result == ["10.5281", "10.5072"]


def test_429_retries_after_retry_after_header(monkeypatch):
    c = DataCiteHTTPClient(host="api.datacite.org", tokens=[])
    responses = [
        _make_response(429, headers={"Retry-After": "1"}),
        _make_response(200, {"data": {"id": "10.x/y", "attributes": {}}}),
    ]
    sleeps = []
    monkeypatch.setattr(
        "open_pulse_crawler.platforms.datacite_adapter.client.time.sleep",
        lambda s: sleeps.append(s),
    )
    with patch.object(c._session, "get", side_effect=responses):
        result = c.get_doi("10.x/y")
    assert result == {"id": "10.x/y", "attributes": {}}
    assert sleeps == [1.0]


def test_429_caps_retry_after_at_60s(monkeypatch):
    c = DataCiteHTTPClient(host="api.datacite.org", tokens=[])
    sleeps = []
    monkeypatch.setattr(
        "open_pulse_crawler.platforms.datacite_adapter.client.time.sleep",
        lambda s: sleeps.append(s),
    )
    responses = [_make_response(429, headers={"Retry-After": "9999"}),
                 _make_response(200, {"data": {"id": "x", "attributes": {}}})]
    with patch.object(c._session, "get", side_effect=responses):
        c.get_doi("x")
    assert sleeps == [60.0]


def test_429_twice_raises(monkeypatch):
    c = DataCiteHTTPClient(host="api.datacite.org", tokens=[])
    monkeypatch.setattr(
        "open_pulse_crawler.platforms.datacite_adapter.client.time.sleep",
        lambda s: None,
    )
    responses = [_make_response(429, headers={"Retry-After": "0"}),
                 _make_response(429)]
    with patch.object(c._session, "get", side_effect=responses):
        with pytest.raises(httpx.HTTPStatusError):
            c.get_doi("x")


def test_iter_dois_by_ror_single_page():
    c = DataCiteHTTPClient(host="api.datacite.org", tokens=[])
    body = {
        "data": [
            {"id": "10.1/x", "attributes": {"doi": "10.1/x"}},
            {"id": "10.2/y", "attributes": {"doi": "10.2/y"}},
        ],
        "links": {},
        "meta": {"total": 2},
    }
    with patch.object(c._session, "get", return_value=_make_response(200, body)) as g:
        works = list(c.iter_dois_by_ror("https://ror.org/02s376052"))
    g.assert_called_once_with(
        "/dois",
        params={
            "query": 'creators.affiliation.affiliationIdentifier:"https://ror.org/02s376052"',
            "page[size]": 100,
            "page[cursor]": 1,
        },
    )
    assert [w["id"] for w in works] == ["10.1/x", "10.2/y"]


def test_iter_dois_by_ror_follows_links_next():
    c = DataCiteHTTPClient(host="api.datacite.org", tokens=[])
    page1 = {
        "data": [{"id": "10.1/x", "attributes": {}}],
        "links": {"next": "https://api.datacite.org/dois?query=...&page[cursor]=2"},
    }
    page2 = {
        "data": [{"id": "10.2/y", "attributes": {}}],
        "links": {},
    }
    with patch.object(c._session, "get") as g:
        g.side_effect = [_make_response(200, page1), _make_response(200, page2)]
        works = list(c.iter_dois_by_ror("https://ror.org/02s376052"))
    assert [w["id"] for w in works] == ["10.1/x", "10.2/y"]
    assert g.call_count == 2


def test_iter_dois_by_orcid_query_shape():
    c = DataCiteHTTPClient(host="api.datacite.org", tokens=[])
    body = {"data": [], "links": {}}
    with patch.object(c._session, "get", return_value=_make_response(200, body)) as g:
        list(c.iter_dois_by_orcid("https://orcid.org/0000-0002-1825-0097"))
    g.assert_called_once_with(
        "/dois",
        params={
            "query": 'creators.nameIdentifiers.nameIdentifier:"https://orcid.org/0000-0002-1825-0097"',
            "page[size]": 100,
            "page[cursor]": 1,
        },
    )
