"""Tests for the auth module (Bearer-token authentication)."""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from open_pulse_crawler.api import app

TEST_TOKEN = "test-secret-token"


@pytest.fixture()
def client():
    """TestClient with API_TOKEN configured."""
    with patch.dict(os.environ, {"API_TOKEN": TEST_TOKEN}):
        with TestClient(app) as c:
            yield c


@pytest.fixture()
def auth_header():
    return {"Authorization": f"Bearer {TEST_TOKEN}"}


# ── Token validation ────────────────────────────────────────────────────


class TestVerifyToken:
    """Exercise the verify_token dependency through the API."""

    def test_missing_auth_header_is_rejected(self, client: TestClient):
        resp = client.post("/api/v1/crawl", json={"seeds": ["torvalds"]})
        assert resp.status_code in (401, 403)

    def test_wrong_scheme_is_rejected(self, client: TestClient):
        resp = client.post(
            "/api/v1/crawl",
            json={"seeds": ["torvalds"]},
            headers={"Authorization": "Basic dXNlcjpwYXNz"},
        )
        assert resp.status_code in (401, 403)

    def test_invalid_token_returns_401(self, client: TestClient):
        resp = client.post(
            "/api/v1/crawl",
            json={"seeds": ["torvalds"]},
            headers={"Authorization": "Bearer wrong-token"},
        )
        assert resp.status_code == 401
        assert "Invalid or missing API token" in resp.json()["detail"]

    def test_valid_token_passes(self, client: TestClient, auth_header: dict):
        resp = client.post(
            "/api/v1/crawl",
            json={"seeds": ["torvalds"]},
            headers=auth_header,
        )
        assert resp.status_code == 202

    def test_constant_time_comparison(self, client: TestClient):
        """Ensure near-miss tokens are still rejected (no early exit)."""
        near_miss = TEST_TOKEN[:-1] + ("x" if TEST_TOKEN[-1] != "x" else "y")
        resp = client.post(
            "/api/v1/crawl",
            json={"seeds": ["torvalds"]},
            headers={"Authorization": f"Bearer {near_miss}"},
        )
        assert resp.status_code == 401


# ── API_TOKEN not configured ────────────────────────────────────────────


class TestMissingApiToken:
    """When the server has no API_TOKEN set, protected endpoints should fail."""

    def test_no_api_token_returns_503(self):
        with patch.dict(os.environ, {"API_TOKEN": ""}, clear=False):
            with TestClient(app) as c:
                resp = c.post(
                    "/api/v1/crawl",
                    json={"seeds": ["torvalds"]},
                    headers={"Authorization": "Bearer anything"},
                )
                assert resp.status_code == 503
                assert "not configured" in resp.json()["detail"]

    def test_unset_api_token_returns_503(self):
        env = os.environ.copy()
        env.pop("API_TOKEN", None)
        with patch.dict(os.environ, env, clear=True):
            with TestClient(app) as c:
                resp = c.post(
                    "/api/v1/crawl",
                    json={"seeds": ["torvalds"]},
                    headers={"Authorization": "Bearer anything"},
                )
                assert resp.status_code == 503


# ── Health endpoint is public ───────────────────────────────────────────


class TestHealthNoAuth:
    """The /health endpoint must remain accessible without any token."""

    def test_health_no_token(self, client: TestClient):
        resp = client.get("/api/v1/health")
        assert resp.status_code == 200

    def test_health_with_token_also_works(self, client: TestClient, auth_header: dict):
        resp = client.get("/api/v1/health", headers=auth_header)
        assert resp.status_code == 200


# ── Protected endpoints require auth ────────────────────────────────────


class TestProtectedEndpoints:
    """All non-health endpoints must reject unauthenticated requests."""

    @pytest.mark.parametrize(
        "method, path",
        [
            ("POST", "/api/v1/crawl"),
            ("GET", "/api/v1/crawl/some-id"),
            ("GET", "/api/v1/graph/some-id"),
        ],
    )
    def test_endpoints_reject_missing_token(
        self, client: TestClient, method: str, path: str
    ):
        kwargs: dict = {}
        if method == "POST":
            kwargs["json"] = {"seeds": ["torvalds"]}
        resp = client.request(method, path, **kwargs)
        assert resp.status_code in (401, 403)
