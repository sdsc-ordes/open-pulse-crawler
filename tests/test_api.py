"""Tests for the FastAPI REST API."""

import os
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from open_pulse_crawler.api import JobStatus, _jobs, app

TEST_TOKEN = "test-secret-token"


@pytest.fixture(autouse=True)
def _clear_jobs():
    """Reset the in-memory job store between tests."""
    _jobs.clear()
    yield
    _jobs.clear()


@pytest.fixture()
def client():
    """TestClient with API_TOKEN set."""
    with patch.dict(os.environ, {"API_TOKEN": TEST_TOKEN}):
        with TestClient(app) as c:
            yield c


@pytest.fixture()
def auth_header():
    return {"Authorization": f"Bearer {TEST_TOKEN}"}


# ── Health endpoint ──────────────────────────────────────────────────────


class TestHealth:
    def test_health_returns_ok(self, client: TestClient):
        resp = client.get("/api/v1/health")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert "version" in body

    def test_health_requires_no_auth(self, client: TestClient):
        resp = client.get("/api/v1/health")
        assert resp.status_code == 200


# ── Auth ─────────────────────────────────────────────────────────────────


class TestAuth:
    def test_missing_token_is_rejected(self, client: TestClient):
        resp = client.post("/api/v1/crawl", json={"seeds": ["torvalds"]})
        assert resp.status_code in (401, 403)

    def test_invalid_token_returns_401(self, client: TestClient):
        resp = client.post(
            "/api/v1/crawl",
            json={"seeds": ["torvalds"]},
            headers={"Authorization": "Bearer wrong-token"},
        )
        assert resp.status_code == 401

    def test_valid_token_is_accepted(self, client: TestClient, auth_header: dict):
        resp = client.post(
            "/api/v1/crawl",
            json={"seeds": ["torvalds"]},
            headers=auth_header,
        )
        assert resp.status_code == 202


# ── Crawl endpoint ───────────────────────────────────────────────────────


class TestCrawl:
    def test_start_crawl_returns_202(self, client: TestClient, auth_header: dict):
        resp = client.post(
            "/api/v1/crawl",
            json={"seeds": ["torvalds"], "max_rounds": 1},
            headers=auth_header,
        )
        assert resp.status_code == 202
        body = resp.json()
        assert "job_id" in body
        assert body["status"] == "pending"

    def test_start_crawl_requires_seeds(self, client: TestClient, auth_header: dict):
        resp = client.post(
            "/api/v1/crawl",
            json={"seeds": []},
            headers=auth_header,
        )
        assert resp.status_code == 422

    def test_max_rounds_bounds(self, client: TestClient, auth_header: dict):
        resp = client.post(
            "/api/v1/crawl",
            json={"seeds": ["a"], "max_rounds": 0},
            headers=auth_header,
        )
        assert resp.status_code == 422

        resp = client.post(
            "/api/v1/crawl",
            json={"seeds": ["a"], "max_rounds": 11},
            headers=auth_header,
        )
        assert resp.status_code == 422

    def test_new_parameter_bounds(self, client: TestClient, auth_header: dict):
        resp = client.post(
            "/api/v1/crawl",
            json={"seeds": ["a"], "min_stars": -1},
            headers=auth_header,
        )
        assert resp.status_code == 422

        resp = client.post(
            "/api/v1/crawl",
            json={"seeds": ["a"], "max_dependents": 0},
            headers=auth_header,
        )
        assert resp.status_code == 422

        resp = client.post(
            "/api/v1/crawl",
            json={"seeds": ["a"], "batch_size": 0},
            headers=auth_header,
        )
        assert resp.status_code == 422

    def test_missing_github_token_marks_job_failed(
        self, client: TestClient, auth_header: dict
    ):
        with patch.dict(os.environ, {"GITHUB_TOKEN": ""}, clear=False):
            start = client.post(
                "/api/v1/crawl",
                json={"seeds": ["torvalds"], "max_rounds": 1},
                headers=auth_header,
            )
            assert start.status_code == 202
            job_id = start.json()["job_id"]

            status_resp = client.get(f"/api/v1/crawl/{job_id}", headers=auth_header)
            assert status_resp.status_code == 200
            body = status_resp.json()
            assert body["status"] == JobStatus.FAILED.value
            assert "GITHUB_TOKEN" in (body.get("detail") or "")

    def test_bad_github_credentials_marks_job_failed(
        self, client: TestClient, auth_header: dict
    ):
        with patch.dict(os.environ, {"GITHUB_TOKEN": "ghp_valid_format_token"}, clear=False):
            with patch("open_pulse_crawler.github_client.GitHubClient"):
                with patch("open_pulse_crawler.crawler.GitHubCrawler") as crawler_cls:
                    crawler = crawler_cls.return_value
                    crawler.add_seeds.return_value = None
                    crawler.crawl.side_effect = Exception("Bad credentials")

                    start = client.post(
                        "/api/v1/crawl",
                        json={"seeds": ["torvalds"], "max_rounds": 1},
                        headers=auth_header,
                    )
                    assert start.status_code == 202
                    job_id = start.json()["job_id"]

                    status_resp = client.get(
                        f"/api/v1/crawl/{job_id}",
                        headers=auth_header,
                    )
                    assert status_resp.status_code == 200
                    body = status_resp.json()
                    assert body["status"] == JobStatus.FAILED.value
                    assert "Bad credentials" in (body.get("detail") or "")

    def test_crawl_passes_dependents_and_epfl_to_crawler(
        self, client: TestClient, auth_header: dict
    ):
        request_body = {
            "seeds": ["torvalds"],
            "max_rounds": 1,
            "crawl_dependencies": True,
            "crawl_dependents": True,
            "min_stars": 25,
            "max_dependents": 50,
            "batch_size": 4,
            "epfl_entities": ["epfl", "dslab-epfl"],
        }

        with patch.dict(os.environ, {"GITHUB_TOKEN": "ghp_valid_format_token"}, clear=False):
            with patch("open_pulse_crawler.github_client.GitHubClient"):
                with patch("open_pulse_crawler.crawler.GitHubCrawler") as crawler_cls:
                    crawler = crawler_cls.return_value
                    crawler.add_seeds.return_value = None
                    crawler.crawl.return_value = None
                    crawler.graph.users = {}
                    crawler.graph.orgs = {}
                    crawler.graph.repos = {}

                    resp = client.post(
                        "/api/v1/crawl",
                        json=request_body,
                        headers=auth_header,
                    )

                    assert resp.status_code == 202
                    crawler_cls.assert_called_once()
                    call_kwargs = crawler_cls.call_args.kwargs
                    assert call_kwargs["max_rounds"] == 1
                    assert call_kwargs["crawl_dependencies"] is True
                    assert call_kwargs["crawl_dependents"] is True
                    assert call_kwargs["min_stars"] == 25
                    assert call_kwargs["max_dependents"] == 50
                    assert call_kwargs["batch_size"] == 4
                    assert call_kwargs["epfl_entities"] == {"epfl", "dslab-epfl"}

    def test_crawl_passes_gimie_options_to_crawler(
        self, client: TestClient, auth_header: dict, tmp_path
    ):
        request_body = {
            "seeds": ["sdsc-ordes/gimie"],
            "max_rounds": 1,
            "gimie_repos": True,
            "gimie_api_base": "http://example.invalid:1234",
            "gimie_store_jsonld": True,
            "gimie_skip_existing_jsonld": True,
            "gimie_archive_on_download": False,
        }

        with patch.dict(
            os.environ,
            {"GITHUB_TOKEN": "ghp_valid_format_token", "OPC_DATA_DIR": str(tmp_path)},
            clear=False,
        ):
            with patch("open_pulse_crawler.github_client.GitHubClient"):
                with patch("open_pulse_crawler.crawler.GitHubCrawler") as crawler_cls:
                    crawler = crawler_cls.return_value
                    crawler.add_seeds.return_value = None
                    crawler.crawl.return_value = None
                    crawler.graph.users = {}
                    crawler.graph.orgs = {}
                    crawler.graph.repos = {}

                    resp = client.post(
                        "/api/v1/crawl",
                        json=request_body,
                        headers=auth_header,
                    )

                    assert resp.status_code == 202
                    job_id = resp.json()["job_id"]

                    crawler_cls.assert_called_once()
                    call_kwargs = crawler_cls.call_args.kwargs
                    assert call_kwargs["gimie_repos"] is True
                    assert call_kwargs["gimie_api_base"] == request_body["gimie_api_base"]
                    assert call_kwargs["gimie_skip_existing_jsonld"] is True
                    assert call_kwargs["gimie_store_jsonld_dir"] == (
                        tmp_path / job_id / "jsonld"
                    )


# ── Job status endpoint ──────────────────────────────────────────────────


class TestJobStatus:
    def test_get_nonexistent_job_returns_404(self, client: TestClient, auth_header: dict):
        resp = client.get("/api/v1/crawl/no-such-id", headers=auth_header)
        assert resp.status_code == 404

    def test_get_job_status(self, client: TestClient, auth_header: dict):
        post_resp = client.post(
            "/api/v1/crawl",
            json={"seeds": ["torvalds"]},
            headers=auth_header,
        )
        job_id = post_resp.json()["job_id"]

        resp = client.get(f"/api/v1/crawl/{job_id}", headers=auth_header)
        assert resp.status_code == 200
        body = resp.json()
        assert body["job_id"] == job_id
        assert body["status"] in [s.value for s in JobStatus]


# ── Graph endpoint ───────────────────────────────────────────────────────


class TestGraph:
    def test_graph_nonexistent_job_returns_404(self, client: TestClient, auth_header: dict):
        resp = client.get("/api/v1/graph/no-such-id", headers=auth_header)
        assert resp.status_code == 404

    def test_graph_not_completed_returns_409(self, client: TestClient, auth_header: dict):
        from open_pulse_crawler.api import _JobRecord

        _jobs["test-job"] = _JobRecord(status=JobStatus.RUNNING)
        resp = client.get("/api/v1/graph/test-job", headers=auth_header)
        assert resp.status_code == 409

    def test_graph_completed_job(self, client: TestClient, auth_header: dict):
        from open_pulse_crawler.api import _JobRecord
        from open_pulse_crawler.models import GraphData

        _jobs["done-job"] = _JobRecord(status=JobStatus.COMPLETED, graph=GraphData())
        resp = client.get("/api/v1/graph/done-job", headers=auth_header)
        assert resp.status_code == 200
        body = resp.json()
        assert body["job_id"] == "done-job"
        assert "graph" in body
        assert body["graph"]["users"] == {}
