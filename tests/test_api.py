"""Tests for the FastAPI REST API."""

import json
import os
import threading
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from open_pulse_crawler.api import JobStatus, _jobs, app

TEST_TOKEN = "test-secret-token"


@pytest.fixture(autouse=True)
def _clear_jobs():
    """Reset the in-memory job store and stop signals between tests."""
    from open_pulse_crawler.api import _stop_events

    _jobs.clear()
    _stop_events.clear()
    yield
    _jobs.clear()
    _stop_events.clear()


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
        assert body["partial"] is False


class TestGraphPartial:
    """Partial-graph recovery: ?partial=true and per-round disk snapshots."""

    def test_partial_not_requested_keeps_strict_409(
        self, client: TestClient, auth_header: dict
    ):
        from open_pulse_crawler.api import _JobRecord

        _jobs["run-job"] = _JobRecord(status=JobStatus.RUNNING)
        resp = client.get("/api/v1/graph/run-job", headers=auth_header)
        assert resp.status_code == 409
        assert "partial=true" in resp.json()["detail"]

    def test_partial_running_job_reads_snapshot(
        self, client: TestClient, auth_header: dict, tmp_path
    ):
        from open_pulse_crawler.api import _JobRecord, _write_snapshot
        from open_pulse_crawler.models import GraphData, UserModel

        with patch.dict(os.environ, {"OPC_DATA_DIR": str(tmp_path)}):
            graph = GraphData()
            graph.add_user(UserModel(login="alice", id=1))
            _write_snapshot("run-job", graph, JobStatus.RUNNING, 2)
            _jobs["run-job"] = _JobRecord(status=JobStatus.RUNNING, rounds_completed=2)
            resp = client.get("/api/v1/graph/run-job?partial=true", headers=auth_header)

        assert resp.status_code == 200
        body = resp.json()
        assert body["partial"] is True
        assert body["status"] == "running"
        assert body["rounds_completed"] == 2
        assert "alice" in body["graph"]["users"]

    def test_partial_failed_job_reads_snapshot(
        self, client: TestClient, auth_header: dict, tmp_path
    ):
        from open_pulse_crawler.api import _JobRecord, _write_snapshot
        from open_pulse_crawler.models import GraphData, RepoModel

        with patch.dict(os.environ, {"OPC_DATA_DIR": str(tmp_path)}):
            graph = GraphData()
            graph.add_repo(RepoModel(full_name="o/r", id=9, owner="o"))
            _write_snapshot("failed-job", graph, JobStatus.FAILED, 1)
            _jobs["failed-job"] = _JobRecord(
                status=JobStatus.FAILED, detail="boom", rounds_completed=1
            )
            resp = client.get("/api/v1/graph/failed-job?partial=true", headers=auth_header)

        assert resp.status_code == 200
        body = resp.json()
        assert body["partial"] is True
        assert body["status"] == "failed"
        assert "o/r" in body["graph"]["repos"]

    def test_restart_recovery_job_not_in_memory(
        self, client: TestClient, auth_header: dict, tmp_path
    ):
        """Snapshot survives loss of the in-memory record (container restart)."""
        from open_pulse_crawler.api import _write_snapshot
        from open_pulse_crawler.models import GraphData, OrgModel

        with patch.dict(os.environ, {"OPC_DATA_DIR": str(tmp_path)}):
            graph = GraphData()
            graph.add_org(OrgModel(login="acme", id=1))
            _write_snapshot("lost-job", graph, JobStatus.RUNNING, 3)
            # Deliberately NOT added to _jobs — simulates a restarted container.
            resp = client.get("/api/v1/graph/lost-job?partial=true", headers=auth_header)

        assert resp.status_code == 200
        body = resp.json()
        assert body["partial"] is True
        assert body["rounds_completed"] == 3
        assert "acme" in body["graph"]["orgs"]

    def test_restart_recovery_requires_partial_flag(
        self, client: TestClient, auth_header: dict, tmp_path
    ):
        """A lost job without ?partial=true still 404s — strict contract holds."""
        from open_pulse_crawler.api import _write_snapshot
        from open_pulse_crawler.models import GraphData

        with patch.dict(os.environ, {"OPC_DATA_DIR": str(tmp_path)}):
            _write_snapshot("lost-job-2", GraphData(), JobStatus.RUNNING, 1)
            resp = client.get("/api/v1/graph/lost-job-2", headers=auth_header)

        assert resp.status_code == 404

    def test_completed_snapshot_flagged_not_partial(
        self, client: TestClient, auth_header: dict, tmp_path
    ):
        """A snapshot whose status is completed reports partial=false even via ?partial."""
        from open_pulse_crawler.api import _write_snapshot
        from open_pulse_crawler.models import GraphData

        with patch.dict(os.environ, {"OPC_DATA_DIR": str(tmp_path)}):
            _write_snapshot("done-disk", GraphData(), JobStatus.COMPLETED, 4)
            resp = client.get("/api/v1/graph/done-disk?partial=true", headers=auth_header)

        assert resp.status_code == 200
        body = resp.json()
        assert body["partial"] is False
        assert body["status"] == "completed"
        assert body["rounds_completed"] == 4

    def test_snapshot_write_read_roundtrip(self, tmp_path):
        from open_pulse_crawler.api import _read_snapshot, _write_snapshot
        from open_pulse_crawler.models import GraphData, OrgModel

        with patch.dict(os.environ, {"OPC_DATA_DIR": str(tmp_path)}):
            graph = GraphData()
            graph.add_org(OrgModel(login="acme", id=1))
            path = _write_snapshot("rt-job", graph, JobStatus.RUNNING, 2)
            assert path is not None
            snap = _read_snapshot("rt-job")

        assert snap is not None
        assert snap["status"] == "running"
        assert snap["rounds_completed"] == 2
        assert "acme" in snap["graph"]["orgs"]

    def test_read_snapshot_missing_returns_none(self, tmp_path):
        from open_pulse_crawler.api import _read_snapshot

        with patch.dict(os.environ, {"OPC_DATA_DIR": str(tmp_path)}):
            assert _read_snapshot("never-existed") is None


class TestStopResume:
    """Cooperative stop and resume-from-state."""

    def test_stop_running_job_sets_event(self, client: TestClient, auth_header: dict):
        from open_pulse_crawler.api import _JobRecord, _stop_events

        _jobs["sj"] = _JobRecord(status=JobStatus.RUNNING)
        event = threading.Event()
        _stop_events["sj"] = event

        resp = client.post("/api/v1/crawl/sj/stop", headers=auth_header)
        assert resp.status_code == 200
        assert event.is_set()
        assert "after the current round" in resp.json()["detail"]

    def test_stop_unknown_job_returns_404(self, client: TestClient, auth_header: dict):
        resp = client.post("/api/v1/crawl/no-such-job/stop", headers=auth_header)
        assert resp.status_code == 404

    def test_stop_completed_job_returns_409(self, client: TestClient, auth_header: dict):
        from open_pulse_crawler.api import _JobRecord

        _jobs["cj"] = _JobRecord(status=JobStatus.COMPLETED)
        resp = client.post("/api/v1/crawl/cj/stop", headers=auth_header)
        assert resp.status_code == 409

    def test_resume_no_persisted_request_returns_404(
        self, client: TestClient, auth_header: dict, tmp_path
    ):
        with patch.dict(os.environ, {"OPC_DATA_DIR": str(tmp_path)}):
            resp = client.post("/api/v1/crawl/ghost/resume", headers=auth_header)
        assert resp.status_code == 404

    def test_resume_no_state_file_returns_409(
        self, client: TestClient, auth_header: dict, tmp_path
    ):
        from open_pulse_crawler.api import CrawlRequest, _persist_request

        with patch.dict(os.environ, {"OPC_DATA_DIR": str(tmp_path)}):
            _persist_request(
                "nostate", CrawlRequest(seeds=["torvalds"], max_rounds=2), mode="rest"
            )
            # request.json exists, state.json does not.
            resp = client.post("/api/v1/crawl/nostate/resume", headers=auth_header)
        assert resp.status_code == 409

    def test_resume_already_running_returns_409(
        self, client: TestClient, auth_header: dict, tmp_path
    ):
        from open_pulse_crawler.api import (
            CrawlRequest,
            _JobRecord,
            _persist_request,
            _state_path,
        )

        with patch.dict(os.environ, {"OPC_DATA_DIR": str(tmp_path)}):
            _persist_request(
                "busy", CrawlRequest(seeds=["torvalds"], max_rounds=2), mode="rest"
            )
            _state_path("busy").write_text("{}")
            _jobs["busy"] = _JobRecord(status=JobStatus.RUNNING)
            resp = client.post("/api/v1/crawl/busy/resume", headers=auth_header)
        assert resp.status_code == 409

    def test_resume_dispatches_with_resume_flag(
        self, client: TestClient, auth_header: dict, tmp_path
    ):
        """A valid resume re-dispatches the crawl on the load_state path."""
        from open_pulse_crawler.api import CrawlRequest, _persist_request, _state_path

        with patch.dict(
            os.environ, {"OPC_DATA_DIR": str(tmp_path), "GITHUB_TOKEN": "ghp_fake"}
        ):
            _persist_request(
                "rj", CrawlRequest(seeds=["torvalds"], max_rounds=2), mode="rest"
            )
            _state_path("rj").write_text("{}")  # presence is all the endpoint checks

            with patch("open_pulse_crawler.github_client.GitHubClient"), patch(
                "open_pulse_crawler.crawler.GitHubCrawler"
            ) as crawler_cls:
                crawler = crawler_cls.return_value
                crawler.current_round = 1
                crawler.load_state.return_value = True
                crawler.crawl.return_value = None
                crawler.graph.users = {}
                crawler.graph.orgs = {}
                crawler.graph.repos = {}

                resp = client.post("/api/v1/crawl/rj/resume", headers=auth_header)

        assert resp.status_code == 202
        # Resume path: load persisted state, do NOT re-seed.
        crawler.load_state.assert_called_once()
        crawler.add_seeds.assert_not_called()

    def test_resume_graphql_mode_uses_graphql_task(
        self, client: TestClient, auth_header: dict, tmp_path
    ):
        """A job persisted with mode=graphql resumes on the GraphQL client."""
        from open_pulse_crawler.api import CrawlRequest, _persist_request, _state_path

        with patch.dict(
            os.environ, {"OPC_DATA_DIR": str(tmp_path), "GITHUB_TOKEN": "ghp_fake"}
        ):
            _persist_request(
                "gj", CrawlRequest(seeds=["torvalds"], max_rounds=2), mode="graphql"
            )
            _state_path("gj").write_text("{}")

            with patch("open_pulse_crawler.graphql_client.GitHubGraphQLClient") as gql_cls, patch(
                "open_pulse_crawler.crawler.GitHubCrawler"
            ) as crawler_cls:
                crawler = crawler_cls.return_value
                crawler.current_round = 1
                crawler.load_state.return_value = True
                crawler.crawl.return_value = None
                crawler.graph.users = {}
                crawler.graph.orgs = {}
                crawler.graph.repos = {}

                resp = client.post("/api/v1/crawl/gj/resume", headers=auth_header)

        assert resp.status_code == 202
        # The GraphQL client was constructed — confirms the graphql task ran.
        gql_cls.assert_called_once()
        crawler.load_state.assert_called_once()
