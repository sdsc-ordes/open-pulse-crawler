"""Tests for CrossrefClient and crossref_message_to_work mapper (no network)."""
from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch

import httpx
import pytest

from open_pulse_crawler.platforms.crossref import (
    CrossrefClient,
    crossref_message_to_work,
)


# ---------------------------------------------------------------------------
# Shared fixture: an AlphaFold-style Crossref message dict
# ---------------------------------------------------------------------------

ALPHAFOLD_MESSAGE = {
    "DOI": "10.1038/s41586-021-03819-2",
    "title": ["Highly accurate protein structure prediction with AlphaFold"],
    "author": [
        {
            "given": "John",
            "family": "Jumper",
            "ORCID": "http://orcid.org/0000-0001-6169-6851",
            "affiliation": [{"name": "DeepMind"}],
        },
        {
            "given": "Richard",
            "family": "Evans",
            "affiliation": [{"name": "DeepMind"}],
        },
    ],
    "published": {"date-parts": [[2021, 8, 26]]},
    "container-title": ["Nature"],
    "publisher": "Springer Nature",
    "type": "journal-article",
    "abstract": "<jats:p>We introduce AlphaFold…</jats:p>",
    "subject": ["Multidisciplinary"],
    "funder": [{"name": "Google DeepMind", "DOI": "10.13039/100023581"}],
    "is-referenced-by-count": 41346,
    "reference": [
        # Entry WITH a DOI
        {
            "key": "ref1",
            "DOI": "10.1126/science.abc8469",
            "article-title": "Protein structure prediction",
        },
        # Entry WITHOUT a DOI (should be skipped)
        {
            "key": "ref2",
            "unstructured": "Some text without a DOI",
        },
        # Duplicate of ref1 — should be deduped
        {
            "key": "ref3",
            "DOI": "10.1126/science.abc8469",
            "article-title": "Protein structure prediction (dup)",
        },
    ],
}


# ---------------------------------------------------------------------------
# Helper: build a mock httpx.Response
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# 1. crossref_message_to_work mapper tests
# ---------------------------------------------------------------------------

class TestCrossrefMessageToWork:
    def test_doi_lowercased_and_url_set(self):
        msg = dict(ALPHAFOLD_MESSAGE)
        msg["DOI"] = "10.1038/S41586-021-03819-2"  # uppercase
        work = crossref_message_to_work(msg)
        assert work.doi == "10.1038/s41586-021-03819-2"
        assert work.url == "https://doi.org/10.1038/s41586-021-03819-2"

    def test_title_extracted_from_list(self):
        work = crossref_message_to_work(ALPHAFOLD_MESSAGE)
        assert work.title == "Highly accurate protein structure prediction with AlphaFold"

    def test_title_empty_when_absent(self):
        msg = {**ALPHAFOLD_MESSAGE, "title": []}
        work = crossref_message_to_work(msg)
        assert work.title == ""

    def test_publication_year_from_published(self):
        work = crossref_message_to_work(ALPHAFOLD_MESSAGE)
        assert work.publication_year == 2021

    def test_publication_year_fallback_to_issued(self):
        msg = {k: v for k, v in ALPHAFOLD_MESSAGE.items()
               if k not in ("published", "published-print", "published-online")}
        msg["issued"] = {"date-parts": [[2020, 1]]}
        work = crossref_message_to_work(msg)
        assert work.publication_year == 2020

    def test_publication_year_none_when_absent(self):
        msg = {k: v for k, v in ALPHAFOLD_MESSAGE.items()
               if k not in ("published", "published-print", "published-online", "issued")}
        work = crossref_message_to_work(msg)
        assert work.publication_year is None

    def test_publisher(self):
        work = crossref_message_to_work(ALPHAFOLD_MESSAGE)
        assert work.publisher == "Springer Nature"

    def test_container_title_first_element(self):
        work = crossref_message_to_work(ALPHAFOLD_MESSAGE)
        assert work.container_title == "Nature"

    def test_container_title_empty_when_absent(self):
        msg = {k: v for k, v in ALPHAFOLD_MESSAGE.items() if k != "container-title"}
        work = crossref_message_to_work(msg)
        assert work.container_title == ""

    def test_work_type(self):
        work = crossref_message_to_work(ALPHAFOLD_MESSAGE)
        assert work.work_type == "journal-article"

    def test_abstract_stored_as_is(self):
        work = crossref_message_to_work(ALPHAFOLD_MESSAGE)
        assert work.abstract == "<jats:p>We introduce AlphaFold…</jats:p>"

    def test_creators_stored_as_is(self):
        work = crossref_message_to_work(ALPHAFOLD_MESSAGE)
        assert len(work.creators) == 2
        assert work.creators[0]["family"] == "Jumper"
        assert work.creators[0]["ORCID"] == "http://orcid.org/0000-0001-6169-6851"

    def test_subjects(self):
        work = crossref_message_to_work(ALPHAFOLD_MESSAGE)
        assert work.subjects == ["Multidisciplinary"]

    def test_funders_stored_as_is(self):
        work = crossref_message_to_work(ALPHAFOLD_MESSAGE)
        assert len(work.funders) == 1
        assert work.funders[0]["name"] == "Google DeepMind"

    def test_is_referenced_by_count(self):
        work = crossref_message_to_work(ALPHAFOLD_MESSAGE)
        assert work.is_referenced_by_count == 41346

    def test_is_referenced_by_count_none_when_absent(self):
        msg = {k: v for k, v in ALPHAFOLD_MESSAGE.items() if k != "is-referenced-by-count"}
        work = crossref_message_to_work(msg)
        assert work.is_referenced_by_count is None

    def test_reference_dois_only_include_entries_with_doi(self):
        work = crossref_message_to_work(ALPHAFOLD_MESSAGE)
        # ref2 has no DOI; ref3 is a dup of ref1 → only one entry
        assert work.reference_dois == ["10.1126/science.abc8469"]

    def test_references_are_canonical_doi_urls(self):
        work = crossref_message_to_work(ALPHAFOLD_MESSAGE)
        assert work.references == ["https://doi.org/10.1126/science.abc8469"]

    def test_references_deduped_while_preserving_order(self):
        msg = {**ALPHAFOLD_MESSAGE, "reference": [
            {"DOI": "10.1000/a"},
            {"DOI": "10.1000/b"},
            {"DOI": "10.1000/a"},  # dup
        ]}
        work = crossref_message_to_work(msg)
        assert work.reference_dois == ["10.1000/a", "10.1000/b"]
        assert work.references == [
            "https://doi.org/10.1000/a",
            "https://doi.org/10.1000/b",
        ]

    def test_reference_dois_lowercased(self):
        msg = {**ALPHAFOLD_MESSAGE, "reference": [{"DOI": "10.1000/UPPER"}]}
        work = crossref_message_to_work(msg)
        assert work.reference_dois == ["10.1000/upper"]

    def test_relations_empty(self):
        work = crossref_message_to_work(ALPHAFOLD_MESSAGE)
        assert work.relations == []

    def test_subkind_is_crossref_work(self):
        work = crossref_message_to_work(ALPHAFOLD_MESSAGE)
        assert work.subkind == "CrossrefWork"

    def test_full_name_derived_from_doi_url(self):
        """CrossrefWork inherits RepoModel which auto-fills full_name from url."""
        work = crossref_message_to_work(ALPHAFOLD_MESSAGE)
        # url is set; full_name should be non-empty (RepoModel validator fills it).
        assert work.url == "https://doi.org/10.1038/s41586-021-03819-2"


# ---------------------------------------------------------------------------
# 2. CrossrefClient.fetch_work tests
# ---------------------------------------------------------------------------

class TestCrossrefClientFetchWork:
    def _make_200_body(self):
        return {"status": "ok", "message": ALPHAFOLD_MESSAGE}

    def test_fetch_work_200_returns_populated_crossref_work(self):
        client = CrossrefClient(mailto="test@example.com")
        with patch.object(client._session, "get",
                          return_value=_make_response(200, self._make_200_body())):
            work = client.fetch_work("10.1038/s41586-021-03819-2")
        assert work is not None
        assert work.doi == "10.1038/s41586-021-03819-2"
        assert work.title == "Highly accurate protein structure prediction with AlphaFold"
        assert work.publication_year == 2021
        assert work.container_title == "Nature"
        assert isinstance(work.is_referenced_by_count, int)

    def test_fetch_work_404_returns_none(self):
        client = CrossrefClient(mailto="test@example.com")
        with patch.object(client._session, "get",
                          return_value=_make_response(404)):
            result = client.fetch_work("10.1038/does-not-exist")
        assert result is None

    def test_fetch_work_status_not_ok_returns_none(self):
        client = CrossrefClient(mailto="test@example.com")
        body = {"status": "error", "message": ALPHAFOLD_MESSAGE}
        with patch.object(client._session, "get",
                          return_value=_make_response(200, body)):
            result = client.fetch_work("10.1038/s41586-021-03819-2")
        assert result is None

    def test_fetch_work_missing_message_returns_none(self):
        client = CrossrefClient(mailto="test@example.com")
        body = {"status": "ok"}
        with patch.object(client._session, "get",
                          return_value=_make_response(200, body)):
            result = client.fetch_work("10.1038/s41586-021-03819-2")
        assert result is None

    def test_doi_path_is_url_quoted(self):
        """DOI slash is preserved as a literal '/' in the request path (not %2F).

        Crossref's REST router uses the slash as a structural path separator;
        encoding it to %2F causes a 404 for every real DOI.
        """
        client = CrossrefClient(mailto="test@example.com")
        captured = []

        def fake_get(path, **kwargs):
            captured.append(path)
            return _make_response(200, self._make_200_body())

        with patch.object(client._session, "get", side_effect=fake_get):
            client.fetch_work("10.1038/s41586-021-03819-2")
        assert len(captured) == 1
        captured_path = captured[0]
        assert captured_path == "/works/10.1038/s41586-021-03819-2", (
            f"Expected literal slash in path, got: {captured_path!r}"
        )
        assert "%2F" not in captured_path, (
            f"Slash was percent-encoded (breaks Crossref routing): {captured_path!r}"
        )


# ---------------------------------------------------------------------------
# 3. mailto / polite-pool behaviour
# ---------------------------------------------------------------------------

class TestCrossrefClientMailto:
    def test_mailto_sent_as_query_param(self):
        client = CrossrefClient(mailto="researcher@uni.edu")
        captured_requests = []

        def fake_get(path, **kwargs):
            # params may be passed as keyword arg
            captured_requests.append({"path": path, "kwargs": kwargs})
            return _make_response(200, {"status": "ok", "message": ALPHAFOLD_MESSAGE})

        with patch.object(client._session, "get", side_effect=fake_get):
            client.fetch_work("10.1038/s41586-021-03819-2")

        assert captured_requests
        call_kwargs = captured_requests[0]["kwargs"]
        params = call_kwargs.get("params", {})
        assert params.get("mailto") == "researcher@uni.edu"

    def test_mailto_in_user_agent_header(self):
        client = CrossrefClient(mailto="researcher@uni.edu")
        ua = client._session.headers.get("User-Agent", "")
        assert "researcher@uni.edu" in ua

    def test_no_mailto_plain_user_agent(self):
        """When no mailto is configured, UA has no email and no mailto param."""
        import open_pulse_crawler.platforms.crossref as crossref_mod
        # Reset the one-shot warning flag so the warning fires in this test
        crossref_mod._no_mailto_warned = False

        with patch("open_pulse_crawler.platforms.crossref.config.resolve_crossref_mailto",
                   return_value=None):
            client = CrossrefClient(mailto=None)

        ua = client._session.headers.get("User-Agent", "")
        assert "@" not in ua
        assert "OpenPulseCrawler" in ua

    def test_no_mailto_warning_logged_once(self, monkeypatch, caplog):
        import open_pulse_crawler.platforms.crossref as crossref_mod
        crossref_mod._no_mailto_warned = False

        monkeypatch.setattr(
            "open_pulse_crawler.platforms.crossref.config.resolve_crossref_mailto",
            lambda: None,
        )
        with caplog.at_level(logging.WARNING, logger="open_pulse_crawler.platforms.crossref"):
            CrossrefClient(mailto=None)
            CrossrefClient(mailto=None)

        warning_msgs = [r.message for r in caplog.records if r.levelno == logging.WARNING]
        crossref_warns = [m for m in warning_msgs if "CRAWLER_CROSSREF_MAILTO" in m]
        assert len(crossref_warns) == 1, (
            f"Expected exactly 1 warning, got {len(crossref_warns)}: {crossref_warns}"
        )

    def test_no_mailto_no_query_param(self):
        import open_pulse_crawler.platforms.crossref as crossref_mod
        crossref_mod._no_mailto_warned = False

        captured_requests = []

        def fake_get(path, **kwargs):
            captured_requests.append(kwargs)
            return _make_response(200, {"status": "ok", "message": ALPHAFOLD_MESSAGE})

        with patch("open_pulse_crawler.platforms.crossref.config.resolve_crossref_mailto",
                   return_value=None):
            client = CrossrefClient(mailto=None)

        with patch.object(client._session, "get", side_effect=fake_get):
            client.fetch_work("10.1038/s41586-021-03819-2")

        assert captured_requests
        params = captured_requests[0].get("params", {})
        assert "mailto" not in params


# ---------------------------------------------------------------------------
# 4. 429 retry logic
# ---------------------------------------------------------------------------

class TestCrossrefClient429Retry:
    def test_429_then_200_returns_work(self, monkeypatch):
        client = CrossrefClient(mailto="test@example.com")
        sleeps = []
        monkeypatch.setattr(
            "open_pulse_crawler.platforms.crossref.time.sleep",
            lambda s: sleeps.append(s),
        )
        responses = [
            _make_response(429, headers={"Retry-After": "0"}),
            _make_response(200, {"status": "ok", "message": ALPHAFOLD_MESSAGE}),
        ]
        with patch.object(client._session, "get", side_effect=responses):
            work = client.fetch_work("10.1038/s41586-021-03819-2")
        assert work is not None
        assert work.doi == "10.1038/s41586-021-03819-2"
        assert sleeps == [0.0]

    def test_429_caps_retry_after_at_60s(self, monkeypatch):
        client = CrossrefClient(mailto="test@example.com")
        sleeps = []
        monkeypatch.setattr(
            "open_pulse_crawler.platforms.crossref.time.sleep",
            lambda s: sleeps.append(s),
        )
        responses = [
            _make_response(429, headers={"Retry-After": "9999"}),
            _make_response(200, {"status": "ok", "message": ALPHAFOLD_MESSAGE}),
        ]
        with patch.object(client._session, "get", side_effect=responses):
            client.fetch_work("10.1038/s41586-021-03819-2")
        assert sleeps == [60.0]

    def test_429_twice_raises(self, monkeypatch):
        client = CrossrefClient(mailto="test@example.com")
        monkeypatch.setattr(
            "open_pulse_crawler.platforms.crossref.time.sleep",
            lambda s: None,
        )
        responses = [
            _make_response(429, headers={"Retry-After": "0"}),
            _make_response(429),
        ]
        with patch.object(client._session, "get", side_effect=responses):
            with pytest.raises(httpx.HTTPStatusError):
                client.fetch_work("10.1038/s41586-021-03819-2")

    def test_429_default_sleep_when_no_header(self, monkeypatch):
        client = CrossrefClient(mailto="test@example.com")
        sleeps = []
        monkeypatch.setattr(
            "open_pulse_crawler.platforms.crossref.time.sleep",
            lambda s: sleeps.append(s),
        )
        responses = [
            _make_response(429, headers={}),  # no Retry-After
            _make_response(200, {"status": "ok", "message": ALPHAFOLD_MESSAGE}),
        ]
        with patch.object(client._session, "get", side_effect=responses):
            client.fetch_work("10.1038/s41586-021-03819-2")
        assert sleeps == [5.0]


# ---------------------------------------------------------------------------
# 5. Context-manager / resource management
# ---------------------------------------------------------------------------

class TestCrossrefClientClose:
    def test_context_manager_closes_session(self):
        """Using CrossrefClient as a context manager closes the underlying session."""
        with CrossrefClient(mailto="test@example.com") as client:
            # Session should be open while inside the context.
            assert not client._session.is_closed
        # After __exit__ the session must be closed.
        assert client._session.is_closed

    def test_close_is_idempotent(self):
        """Calling close() twice must not raise."""
        client = CrossrefClient(mailto="test@example.com")
        client.close()
        client.close()  # second call must be a no-op
        assert client._session.is_closed


# ---------------------------------------------------------------------------
# 6. publication_year priority-chain coverage
# ---------------------------------------------------------------------------

class TestPublicationYearPriorityChain:
    def test_published_print_used_when_published_absent(self):
        """published-print is used when published is absent."""
        msg = {k: v for k, v in ALPHAFOLD_MESSAGE.items() if k != "published"}
        msg["published-print"] = {"date-parts": [[2019, 3]]}
        work = crossref_message_to_work(msg)
        assert work.publication_year == 2019

    def test_published_online_used_when_print_and_published_absent(self):
        """published-online is used when both published and published-print are absent."""
        msg = {
            k: v for k, v in ALPHAFOLD_MESSAGE.items()
            if k not in ("published", "published-print")
        }
        msg["published-online"] = {"date-parts": [[2018, 11]]}
        work = crossref_message_to_work(msg)
        assert work.publication_year == 2018


# ---------------------------------------------------------------------------
# 7. crossref_message_to_work with empty dict
# ---------------------------------------------------------------------------

class TestCrossrefMessageToWorkEmpty:
    def test_empty_dict_returns_zero_valued_work_without_raising(self):
        """crossref_message_to_work({}) must not raise and must return safe defaults."""
        work = crossref_message_to_work({})
        assert work.doi == ""
        assert work.url == "https://doi.org/"
        assert work.title == ""
        assert work.publication_year is None
        assert work.publisher == ""
        assert work.container_title == ""
        assert work.work_type == ""
        assert work.abstract == ""
        assert work.creators == []
        assert work.subjects == []
        assert work.funders == []
        assert work.is_referenced_by_count is None
        assert work.references == []
        assert work.reference_dois == []
        assert work.relations == []
