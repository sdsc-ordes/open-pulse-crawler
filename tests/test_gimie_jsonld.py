import json
import threading
from pathlib import Path
from typing import Any, Dict, Optional
from unittest.mock import patch

from open_pulse_crawler.crawler import GitHubCrawler
from open_pulse_crawler.gimie_client import GimieJsonLdClient
from open_pulse_crawler.gimie_jsonld import parse_gimie_repo_jsonld


def _load_fixture() -> Dict[str, Any]:
    fixture_path = Path(__file__).parent / "gimie-output-example.json"
    with fixture_path.open("r", encoding="utf-8") as f:
        return json.load(f)


def test_parse_gimie_repo_jsonld_extracts_repo_owner_and_contributors():
    payload = _load_fixture()
    parsed = parse_gimie_repo_jsonld(payload)

    assert parsed.repo_full_name == "sdsc-ordes/gimie"
    assert parsed.owner_login == "sdsc-ordes"

    # A few known contributors from the fixture.
    assert "cmdoret" in parsed.contributor_logins
    assert "sabinem" in parsed.contributor_logins
    assert "vancauwe" in parsed.contributor_logins

    assert parsed.login_type_map["sdsc-ordes"] == "org"
    assert parsed.login_type_map["cmdoret"] == "user"


class _DummyGitHubClient:
    def __init__(self) -> None:
        self.semaphore = threading.Semaphore(5)
        self.cache: Optional[Any] = None
        # Not used in this test (dependency/dependent fetching disabled).
        self.tokens = ["dummy-token"]
        self.current_token_idx = 0


def test_crawler_process_repository_uses_gimie_hybrid_when_enabled():
    payload = _load_fixture()

    dummy_client = _DummyGitHubClient()

    with patch(
        "open_pulse_crawler.gimie_client.GimieJsonLdClient.fetch_repo_jsonld",
        return_value=payload,
    ):
        crawler = GitHubCrawler(
            client=dummy_client,
            max_rounds=2,
            crawl_dependencies=False,
            crawl_dependents=False,
            gimie_repos=True,
            gimie_api_base="http://example.invalid",
            gimie_store_jsonld_dir=None,
            gimie_skip_existing_jsonld=True,
        )

        repo = crawler._process_repository("sdsc-ordes/gimie")
        assert repo is not None
        assert repo.full_name == "sdsc-ordes/gimie"
        assert repo.owner == "sdsc-ordes"
        assert "cmdoret" in repo.contributors

        queued_ids = [identifier for _, identifier, _ in crawler.queue]
        assert "sdsc-ordes" in queued_ids
        assert "cmdoret" in queued_ids

        queued_owner_types = [node_type for node_type, identifier, _ in crawler.queue if identifier == "sdsc-ordes"]
        assert "org" in queued_owner_types


def test_gimie_client_http_request_url_includes_force_refresh(tmp_path):
    captured: dict[str, str] = {}

    class FakeResp:
        status_code = 200

        def json(self) -> dict:
            return {"@graph": []}

    class FakeClientCtx:
        def __enter__(self):
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def get(self, url: str) -> FakeResp:
            captured["url"] = url
            return FakeResp()

    with patch("open_pulse_crawler.gimie_client.httpx.Client", return_value=FakeClientCtx()):
        client = GimieJsonLdClient(
            api_base="http://example.invalid",
            jsonld_repo_segment="gimie",
            jsonld_dir=tmp_path,
            skip_existing_jsonld=False,
        )
        client.fetch_repo_jsonld("owner/repo")

    assert "force_refresh=true" in captured["url"]


def test_gimie_client_skip_existing_reads_disk_without_http(tmp_path):
    payload_path = tmp_path / "owner__repo.json"
    payload_path.write_text('{"cached": true}', encoding="utf-8")

    def client_must_not_be_used(*args: object, **kwargs: object) -> None:
        raise AssertionError("httpx.Client should not be used when disk cache hits")

    with patch(
        "open_pulse_crawler.gimie_client.httpx.Client",
        side_effect=client_must_not_be_used,
    ):
        client = GimieJsonLdClient(
            api_base="http://example.invalid",
            jsonld_repo_segment="gimie",
            jsonld_dir=tmp_path,
            skip_existing_jsonld=True,
        )
        out = client.fetch_repo_jsonld("owner/repo")

    assert out == {"cached": True}


def test_gimie_client_success_removes_stale_error_files(tmp_path):
    jsonld_dir = tmp_path / "jsonld"
    jsonld_dir.mkdir(parents=True)
    errors_dir = tmp_path / "jsonld_errors"
    errors_dir.mkdir(parents=True)
    stale = errors_dir / "owner__repo.http_400.json"
    stale.write_text("{}", encoding="utf-8")
    other_error = errors_dir / "other__repo.http_400.json"
    other_error.write_text("{}", encoding="utf-8")

    class FakeResp:
        status_code = 200

        def json(self) -> dict:
            return {"ok": True}

    class FakeClientCtx:
        def __enter__(self):
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def get(self, url: str) -> FakeResp:
            return FakeResp()

    with patch("open_pulse_crawler.gimie_client.httpx.Client", return_value=FakeClientCtx()):
        client = GimieJsonLdClient(
            api_base="http://example.invalid",
            jsonld_repo_segment="gimie",
            jsonld_dir=jsonld_dir,
            skip_existing_jsonld=False,
        )
        out = client.fetch_repo_jsonld("owner/repo")

    assert out == {"ok": True}
    assert not stale.exists()
    assert other_error.exists()
    assert (jsonld_dir / "owner__repo.json").is_file()

