"""Unit tests for the thin httpx wrapper used by the Zenodo adapter.

The wrapper owns construction (anonymous vs. Bearer auth), token rotation,
and the per-host disk-cache hookup. Endpoint methods (single-entity fetches
and iterators) land in Tasks 4 and 5; this test module only covers the
construction / rotation surface defined in Task 3.
"""

from unittest.mock import MagicMock, patch
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
