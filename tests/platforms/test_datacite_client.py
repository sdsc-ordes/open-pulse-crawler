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
