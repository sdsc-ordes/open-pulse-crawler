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


def test_get_model_200_returns_dict():
    c = HuggingFaceHTTPClient(host="huggingface.co", tokens=[])
    payload = {"id": "meta-llama/Llama-3.2-1B", "author": "meta-llama", "downloads": 1000}
    with patch.object(c._session, "get", return_value=_make_response(200, payload)) as g:
        result = c.get_model("meta-llama/Llama-3.2-1B")
    g.assert_called_once_with("/api/models/meta-llama/Llama-3.2-1B")
    assert result == payload


def test_get_model_404_returns_none():
    c = HuggingFaceHTTPClient(host="huggingface.co", tokens=[])
    with patch.object(c._session, "get", return_value=_make_response(404)):
        assert c.get_model("missing/repo") is None


def test_get_dataset_200_returns_dict():
    c = HuggingFaceHTTPClient(host="huggingface.co", tokens=[])
    payload = {"id": "openai/gsm8k", "downloads": 100}
    with patch.object(c._session, "get", return_value=_make_response(200, payload)) as g:
        result = c.get_dataset("openai/gsm8k")
    g.assert_called_once_with("/api/datasets/openai/gsm8k")
    assert result == payload


def test_get_space_200_returns_dict():
    c = HuggingFaceHTTPClient(host="huggingface.co", tokens=[])
    payload = {"id": "black-forest-labs/FLUX.1-schnell", "sdk": "gradio"}
    with patch.object(c._session, "get", return_value=_make_response(200, payload)) as g:
        result = c.get_space("black-forest-labs/FLUX.1-schnell")
    g.assert_called_once_with("/api/spaces/black-forest-labs/FLUX.1-schnell")
    assert result == payload


def test_get_paper_200_takes_first_when_list_returned():
    """HuggingFace's /api/papers/<arxiv-id> sometimes returns a list of dicts
    (one per submission); take the first."""
    c = HuggingFaceHTTPClient(host="huggingface.co", tokens=[])
    payload = [{"id": "2307.09288", "title": "Llama 2"}]
    with patch.object(c._session, "get", return_value=_make_response(200, payload)) as g:
        result = c.get_paper("2307.09288")
    g.assert_called_once_with("/api/papers/2307.09288")
    assert result == {"id": "2307.09288", "title": "Llama 2"}


def test_get_paper_200_dict_passes_through():
    c = HuggingFaceHTTPClient(host="huggingface.co", tokens=[])
    payload = {"id": "2307.09288", "title": "Llama 2"}
    with patch.object(c._session, "get", return_value=_make_response(200, payload)):
        result = c.get_paper("2307.09288")
    assert result == payload


def test_get_collection_200_returns_dict():
    c = HuggingFaceHTTPClient(host="huggingface.co", tokens=[])
    payload = {"slug": "meta-llama/llama-32-...x", "title": "Llama 3.2 evals"}
    with patch.object(c._session, "get", return_value=_make_response(200, payload)) as g:
        result = c.get_collection("meta-llama/llama-32-...x")
    g.assert_called_once_with("/api/collections/meta-llama/llama-32-...x")
    assert result == payload


def test_get_user_overview_200():
    c = HuggingFaceHTTPClient(host="huggingface.co", tokens=[])
    payload = {"user": "karpathy", "type": "user", "fullname": "Andrej Karpathy"}
    with patch.object(c._session, "get", return_value=_make_response(200, payload)) as g:
        result = c.get_user_overview("karpathy")
    g.assert_called_once_with("/api/users/karpathy/overview")
    assert result == payload


def test_get_user_overview_404():
    c = HuggingFaceHTTPClient(host="huggingface.co", tokens=[])
    with patch.object(c._session, "get", return_value=_make_response(404)):
        assert c.get_user_overview("nobody") is None


def test_get_org_overview_200():
    c = HuggingFaceHTTPClient(host="huggingface.co", tokens=[])
    payload = {"name": "meta-llama", "fullname": "Meta Llama"}
    with patch.object(c._session, "get", return_value=_make_response(200, payload)) as g:
        result = c.get_org_overview("meta-llama")
    g.assert_called_once_with("/api/organizations/meta-llama/overview")
    assert result == payload


def test_429_retries_after_retry_after_header(monkeypatch):
    c = HuggingFaceHTTPClient(host="huggingface.co", tokens=[])
    responses = [
        _make_response(429, headers={"Retry-After": "1"}),
        _make_response(200, {"id": "x"}),
    ]
    sleeps = []
    monkeypatch.setattr(
        "open_pulse_crawler.platforms.huggingface.client.time.sleep",
        lambda s: sleeps.append(s),
    )
    with patch.object(c._session, "get", side_effect=responses):
        result = c.get_model("x")
    assert result == {"id": "x"}
    assert sleeps == [1.0]


def test_429_caps_retry_after_at_60s(monkeypatch):
    c = HuggingFaceHTTPClient(host="huggingface.co", tokens=[])
    sleeps = []
    monkeypatch.setattr(
        "open_pulse_crawler.platforms.huggingface.client.time.sleep",
        lambda s: sleeps.append(s),
    )
    responses = [_make_response(429, headers={"Retry-After": "9999"}),
                 _make_response(200, {"id": "x"})]
    with patch.object(c._session, "get", side_effect=responses):
        c.get_model("x")
    assert sleeps == [60.0]


def test_429_twice_raises(monkeypatch):
    c = HuggingFaceHTTPClient(host="huggingface.co", tokens=[])
    monkeypatch.setattr(
        "open_pulse_crawler.platforms.huggingface.client.time.sleep",
        lambda s: None,
    )
    responses = [_make_response(429, headers={"Retry-After": "0"}),
                 _make_response(429)]
    with patch.object(c._session, "get", side_effect=responses):
        with pytest.raises(httpx.HTTPStatusError):
            c.get_model("x")
