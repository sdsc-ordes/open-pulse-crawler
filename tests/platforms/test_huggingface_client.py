"""Tests for the HuggingFace HTTP client wrapper."""
from unittest.mock import MagicMock, patch
import pytest
import httpx

from open_pulse_crawler.platforms.huggingface.client import HuggingFaceHTTPClient


def test_authenticated_sets_bearer_header():
    c = HuggingFaceHTTPClient(host="huggingface.co", tokens=["hf_tok"])
    assert c._session.headers.get("Authorization") == "Bearer hf_tok"


def test_anonymous_sends_no_auth_header():
    c = HuggingFaceHTTPClient(host="huggingface.co", tokens=[])
    assert "Authorization" not in c._session.headers


def test_base_url_uses_https_with_host():
    c = HuggingFaceHTTPClient(host="huggingface.co", tokens=[])
    assert c.base_url == "https://huggingface.co"


def test_session_accepts_json_content_type():
    c = HuggingFaceHTTPClient(host="huggingface.co", tokens=[])
    assert c._session.headers["Accept"] == "application/json"


def test_rotate_in_anonymous_mode_is_noop():
    c = HuggingFaceHTTPClient(host="huggingface.co", tokens=[])
    c._rotate()
    assert "Authorization" not in c._session.headers


def test_rotate_cycles_token_pool():
    c = HuggingFaceHTTPClient(host="huggingface.co", tokens=["a", "b"])
    assert c._session.headers["Authorization"] == "Bearer a"
    c._rotate()
    assert c._session.headers["Authorization"] == "Bearer b"
    c._rotate()
    assert c._session.headers["Authorization"] == "Bearer a"
