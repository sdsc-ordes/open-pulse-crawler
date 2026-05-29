"""Compatibility tests for ``/api/v1`` after the v2 cutover.

Pins two invariants:

1. ``/api/v1/health`` still returns 200 — operators relying on the legacy
   health probe must keep working.
2. ``POST /api/v1/crawl`` and ``POST /api/v1/crawl/graphql`` reject any
   seed whose host is not ``github.com``. The rejection runs *before*
   bearer-token validation so the response is a clean ``400 {"error":
   "...; use /api/v2"}`` rather than the bearer scheme's 401/403.

Bare logins (``torvalds``) and ``owner/repo`` shorthand (``torvalds/linux``)
preserve their existing GitHub-defaulting behaviour. Only full
``https://OTHER.HOST/...`` URLs trip the guard.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from open_pulse_crawler.api import app


client = TestClient(app)


def test_v1_health_unchanged():
    r = client.get("/api/v1/health")
    assert r.status_code == 200


def test_v1_rejects_gitlab_seed():
    r = client.post(
        "/api/v1/crawl",
        json={"seeds": ["https://gitlab.com/foo/bar"], "rounds": 1},
    )
    assert r.status_code == 400
    detail = r.json().get("detail") or r.json().get("error")
    if isinstance(detail, dict):
        assert "v2" in detail.get("error", "").lower()
    else:
        assert "v2" in str(detail).lower()


def test_v1_rejects_renkulab_seed():
    r = client.post(
        "/api/v1/crawl",
        json={"seeds": ["https://renkulab.io/foo"], "rounds": 1},
    )
    assert r.status_code == 400


def test_v1_accepts_bare_login_as_github():
    r = client.post(
        "/api/v1/crawl",
        json={"seeds": ["torvalds"], "rounds": 1},
    )
    # The crawl actually fails for other reasons (no GH token), but the
    # seed guard itself must not reject — status should NOT be 400 with the
    # "v2" message.
    if r.status_code == 400:
        detail = r.json().get("detail") or r.json().get("error")
        if isinstance(detail, dict) and "v2" in str(detail.get("error", "")).lower():
            assert False, "bare login was rejected by the v2-only guard"


def test_v1_accepts_owner_repo_form():
    r = client.post(
        "/api/v1/crawl",
        json={"seeds": ["torvalds/linux"], "rounds": 1},
    )
    if r.status_code == 400:
        detail = r.json().get("detail") or r.json().get("error")
        if isinstance(detail, dict) and "v2" in str(detail.get("error", "")).lower():
            assert False, "owner/repo seed was rejected by the v2-only guard"


def test_v1_accepts_github_url():
    r = client.post(
        "/api/v1/crawl",
        json={"seeds": ["https://github.com/torvalds/linux"], "rounds": 1},
    )
    if r.status_code == 400:
        detail = r.json().get("detail") or r.json().get("error")
        if isinstance(detail, dict) and "v2" in str(detail.get("error", "")).lower():
            assert False, "github.com URL was rejected"


def test_v1_graphql_endpoint_also_guards():
    r = client.post(
        "/api/v1/crawl/graphql",
        json={"seeds": ["https://gitlab.com/foo/bar"], "rounds": 1},
    )
    assert r.status_code == 400
