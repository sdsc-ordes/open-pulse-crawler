"""Smoke tests for the ``/api/v2`` router.

The synchronous GET endpoints (``/health``, ``/platforms``) and the
synchronous parts of POST handlers (request validation) all return without
spawning a background crawl, so ``TestClient`` exercises them safely even
in this sandbox where the BFS background task hangs.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from open_pulse_crawler.api import app


client = TestClient(app)


def test_v2_health():
    r = client.get("/api/v2/health")
    assert r.status_code == 200
    j = r.json()
    assert j["status"] == "ok"
    assert "version" in j


def test_v2_platforms_default_lists_github(monkeypatch):
    monkeypatch.delenv("CRAWLER_PLATFORMS", raising=False)
    r = client.get("/api/v2/platforms")
    assert r.status_code == 200
    hosts = [p["host"] for p in r.json()["platforms"]]
    assert hosts == ["github.com"]


def test_v2_platforms_explicit_list(monkeypatch):
    monkeypatch.setenv("CRAWLER_PLATFORMS", "github.com,gitlab.epfl.ch")
    monkeypatch.setenv("CRAWLER_TOKEN__GITHUB_COM", "ghp_a")
    monkeypatch.setenv("CRAWLER_TOKEN__GITLAB_EPFL_CH", "glpat_x")
    r = client.get("/api/v2/platforms")
    assert r.status_code == 200
    platforms = {p["host"]: p for p in r.json()["platforms"]}
    assert platforms["github.com"]["tokens"] >= 1
    assert platforms["github.com"]["ok"] is True
    assert platforms["gitlab.epfl.ch"]["tokens"] >= 1


def test_v2_platforms_reports_missing_tokens(monkeypatch):
    monkeypatch.setenv("CRAWLER_PLATFORMS", "github.com,renkulab.io")
    monkeypatch.delenv("CRAWLER_TOKEN_POOL__GITHUB_COM", raising=False)
    monkeypatch.delenv("CRAWLER_TOKEN__GITHUB_COM", raising=False)
    monkeypatch.delenv("CRAWLER_GITHUB_TOKEN_POOL", raising=False)
    monkeypatch.delenv("CRAWLER_GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("CRAWLER_TOKEN__RENKULAB_IO", raising=False)
    monkeypatch.delenv("CRAWLER_TOKEN_POOL__RENKULAB_IO", raising=False)
    r = client.get("/api/v2/platforms")
    platforms = {p["host"]: p for p in r.json()["platforms"]}
    assert platforms["renkulab.io"]["tokens"] == 0
    assert platforms["renkulab.io"]["ok"] is False
