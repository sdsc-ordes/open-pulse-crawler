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


def test_v2_crawl_openapi_examples_include_zenodo():
    """Swagger UI's dropdown should surface Zenodo example seeds."""
    from fastapi.testclient import TestClient
    from open_pulse_crawler.api import app
    client = TestClient(app)
    # The app mounts its OpenAPI document at ``/api/v1/openapi.json`` (see
    # ``api/__init__.py``); fall back to ``app.openapi()`` if the URL ever
    # changes. Both surface the same spec — Swagger UI reads it via the URL.
    spec = client.get("/api/v1/openapi.json").json()
    # Find the /api/v2/crawl POST body examples.
    crawl_path = spec["paths"]["/api/v2/crawl"]
    body = crawl_path["post"]["requestBody"]["content"]["application/json"]
    examples = body.get("examples", {})
    # ESCAPE OSSR replaces the never-existed `renku-python` example;
    # `eosc` is the smaller smoke-test community used by the integration test.
    assert "zenodo_community_escape2020" in examples
    assert "zenodo_community_eosc" in examples
    assert "zenodo_record_doi_url" in examples
    assert "zenodo_record_canonical" in examples
    # The Gammapy record is the canonical real-data Zenodo→GitHub example;
    # `cross_platform_escape_full` exercises the full community→GitHub-orgs path.
    assert "cross_platform_zenodo_github_gammapy" in examples
    assert "cross_platform_escape_full" in examples


def test_v2_crawl_openapi_examples_include_infoscience():
    from fastapi.testclient import TestClient
    from open_pulse_crawler.api import app
    client = TestClient(app)
    spec = client.get("/api/v1/openapi.json").json()
    crawl_path = spec["paths"]["/api/v2/crawl"]
    body = crawl_path["post"]["requestBody"]["content"]["application/json"]
    examples = body.get("examples", {})
    assert "infoscience_publication_handle" in examples
    assert "infoscience_person_authored_chain" in examples


def test_v2_crawl_openapi_examples_include_datacite():
    from fastapi.testclient import TestClient
    from open_pulse_crawler.api import app
    client = TestClient(app)
    spec = client.get("/api/v1/openapi.json").json()
    crawl_path = spec["paths"]["/api/v2/crawl"]
    body = crawl_path["post"]["requestBody"]["content"]["application/json"]
    examples = body.get("examples", {})
    assert "datacite_work_by_doi" in examples
    assert "datacite_org_by_ror_epfl" in examples
    assert "datacite_person_by_orcid" in examples


def test_v2_build_registry_from_env_includes_datacite(monkeypatch):
    """`/api/v2/crawl`'s registry-builder must register anonymous adapters
    (Zenodo, Infoscience, DataCite) — previously the v2 path only knew
    GitHub + GitLab and silently skipped every other platform.
    """
    from open_pulse_crawler.api.v2 import _build_registry_from_env
    from open_pulse_crawler.platforms.datacite_adapter.adapter import DataCiteAdapter

    monkeypatch.setenv("CRAWLER_PLATFORMS", "datacite.org")
    monkeypatch.delenv("CRAWLER_TOKEN__API_DATACITE_ORG", raising=False)
    monkeypatch.delenv("CRAWLER_TOKEN_POOL__API_DATACITE_ORG", raising=False)
    registry, _gh = _build_registry_from_env()
    a = registry.adapter_for("https://doi.org/10.6084/m9.figshare.99")
    assert isinstance(a, DataCiteAdapter)


def test_v2_build_registry_from_env_includes_zenodo_and_infoscience(monkeypatch):
    """Same registry parity for Zenodo + Infoscience — both should register
    anonymously through the v2 API path."""
    from open_pulse_crawler.api.v2 import _build_registry_from_env
    from open_pulse_crawler.platforms.zenodo.adapter import ZenodoAdapter
    from open_pulse_crawler.platforms.infoscience.adapter import InfoscienceAdapter

    monkeypatch.setenv("CRAWLER_PLATFORMS", "zenodo.org,infoscience.epfl.ch")
    for var in ("CRAWLER_TOKEN__ZENODO_ORG", "CRAWLER_TOKEN_POOL__ZENODO_ORG",
                "CRAWLER_TOKEN__INFOSCIENCE_EPFL_CH",
                "CRAWLER_TOKEN_POOL__INFOSCIENCE_EPFL_CH"):
        monkeypatch.delenv(var, raising=False)
    registry, _gh = _build_registry_from_env()
    assert isinstance(
        registry.adapter_for("https://zenodo.org/records/42"),
        ZenodoAdapter,
    )
    assert isinstance(
        registry.adapter_for("https://infoscience.epfl.ch/handle/20.500.14299/1"),
        InfoscienceAdapter,
    )


def test_v2_build_registry_from_env_returns_github_client(monkeypatch):
    """Regression (v2 GitHub bug): the v2 path discarded the GitHub client
    (`registry, _gh_client, missing = ...`), leaving `GitHubCrawler.client`
    None. github.com seeds then hit the pass-through `GitHubAdapter.fetch`
    stub (which returns a raw PyGithub object, not a Pydantic model) and were
    silently dropped as "unknown node type". `_build_registry_from_env` must
    surface the GitHub client so `_run_crawl_v2` can pass it to the crawler,
    routing github.com through the model-building legacy path."""
    from open_pulse_crawler.api.v2 import _build_registry_from_env
    from open_pulse_crawler.platforms.github import GitHubClient

    _clear_legacy_github_env(monkeypatch)
    monkeypatch.setenv("CRAWLER_PLATFORMS", "github.com")
    monkeypatch.setenv("CRAWLER_TOKEN__GITHUB_COM", "ghp_fake_for_offline_construction")
    registry, github_client = _build_registry_from_env()
    assert github_client is not None
    assert isinstance(github_client, GitHubClient)
    # And the registry still routes github.com (adapter registered too).
    assert "github.com" in registry.hosts()


def _clear_legacy_github_env(monkeypatch) -> None:
    for var in ("CRAWLER_TOKEN__GITHUB_COM", "CRAWLER_TOKEN_POOL__GITHUB_COM",
                "CRAWLER_GITHUB_TOKEN_POOL", "CRAWLER_GITHUB_TOKEN", "GITHUB_TOKEN"):
        monkeypatch.delenv(var, raising=False)


def test_v2_crawl_openapi_examples_include_huggingface():
    from fastapi.testclient import TestClient
    from open_pulse_crawler.api import app
    client = TestClient(app)
    spec = client.get("/api/v1/openapi.json").json()
    crawl_path = spec["paths"]["/api/v2/crawl"]
    body = crawl_path["post"]["requestBody"]["content"]["application/json"]
    examples = body.get("examples", {})
    assert "huggingface_paper_llama2" in examples
    assert "huggingface_model_llama" in examples
    assert "huggingface_user_karpathy" in examples


def test_v2_crawl_openapi_examples_include_url_keying_demos():
    """Four examples showcasing the URL-as-graph-key convention + the
    cross-platform routing it enables. Added as docs companions to the
    `Node identifiers` section of README.md."""
    from fastapi.testclient import TestClient
    from open_pulse_crawler.api import app
    client = TestClient(app)
    spec = client.get("/api/v1/openapi.json").json()
    crawl_path = spec["paths"]["/api/v2/crawl"]
    body = crawl_path["post"]["requestBody"]["content"]["application/json"]
    examples = body.get("examples", {})
    assert "doi_url_prefix_routing_to_zenodo" in examples
    assert "orcid_url_canonical_seed" in examples
    assert "huggingface_collection_meta_llama" in examples
    assert "mixed_multi_platform_one_job" in examples
    # Sanity: the mixed-platforms example actually seeds 4 different hosts.
    seeds = examples["mixed_multi_platform_one_job"]["value"]["seeds"]
    hosts = {s.split("/")[2] for s in seeds}
    assert hosts == {
        "huggingface.co", "zenodo.org", "ror.org", "github.com",
    }
