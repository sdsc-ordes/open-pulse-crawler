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
