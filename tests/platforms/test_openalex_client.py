"""Tests for OpenAlexHTTPClient (no network — httpx.get is patched).

Mirrors the CrossrefClient test style: a MagicMock-based fake response and
``patch.object(client._session, "get", ...)`` to capture requests.
"""
from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch

import httpx
import pytest

from open_pulse_crawler.platforms.openalex_adapter.client import OpenAlexHTTPClient


# ---------------------------------------------------------------------------
# Helper: build a mock httpx.Response
# ---------------------------------------------------------------------------

def _make_response(status_code: int, json_body=None, headers=None):
    r = MagicMock()
    r.status_code = status_code
    r.is_success = 200 <= status_code < 300
    r.json.return_value = json_body if json_body is not None else {}
    r.headers = headers or {}
    if not (200 <= status_code < 300):
        r.raise_for_status.side_effect = httpx.HTTPStatusError(
            f"{status_code}", request=MagicMock(), response=r,
        )
    else:
        r.raise_for_status.return_value = None
    return r


# ---------------------------------------------------------------------------
# Fixtures: minimal OpenAlex entity dicts
# ---------------------------------------------------------------------------

WORK_FIXTURE = {
    "id": "https://openalex.org/W2741809807",
    "doi": "https://doi.org/10.1038/s41586-021-03819-2",
    "title": "Highly accurate protein structure prediction with AlphaFold",
    "publication_year": 2021,
    "referenced_works": [
        "https://openalex.org/W123",
        "https://openalex.org/W456",
    ],
}

AUTHOR_FIXTURE = {
    "id": "https://openalex.org/A5023888391",
    "orcid": "https://orcid.org/0000-0001-6169-6851",
    "display_name": "John Jumper",
}

INSTITUTION_FIXTURE = {
    "id": "https://openalex.org/I4210090411",
    "ror": "https://ror.org/00971b260",
    "display_name": "DeepMind",
}

SOURCE_FIXTURE = {
    "id": "https://openalex.org/S137773608",
    "display_name": "Nature",
}

FUNDER_FIXTURE = {
    "id": "https://openalex.org/F4320306076",
    "display_name": "Google DeepMind",
}


# ---------------------------------------------------------------------------
# 1. get_work
# ---------------------------------------------------------------------------

class TestGetWork:
    def test_get_work_200_parses_entity(self):
        client = OpenAlexHTTPClient(mailto="test@example.com")
        with patch.object(client._session, "get",
                          return_value=_make_response(200, WORK_FIXTURE)):
            work = client.get_work("W2741809807")
        assert work is not None
        assert work["id"] == "https://openalex.org/W2741809807"
        assert work["doi"] == "https://doi.org/10.1038/s41586-021-03819-2"
        assert work["title"].startswith("Highly accurate")
        assert work["publication_year"] == 2021
        assert work["referenced_works"] == [
            "https://openalex.org/W123",
            "https://openalex.org/W456",
        ]

    def test_get_work_404_returns_none(self):
        client = OpenAlexHTTPClient(mailto="test@example.com")
        with patch.object(client._session, "get",
                          return_value=_make_response(404)):
            assert client.get_work("W_missing") is None

    def test_get_work_doi_id_form_path_quoted(self):
        """A ``doi:10…`` id keeps its colon and slashes (safe=':/')."""
        client = OpenAlexHTTPClient(mailto="test@example.com")
        captured = []

        def fake_get(path, **kwargs):
            captured.append(path)
            return _make_response(200, WORK_FIXTURE)

        with patch.object(client._session, "get", side_effect=fake_get):
            client.get_work("doi:10.1038/s41586-021-03819-2")
        assert captured == ["/works/doi:10.1038/s41586-021-03819-2"]
        assert "%2F" not in captured[0]
        assert "%3A" not in captured[0]

    def test_get_work_https_id_form_quoted(self):
        client = OpenAlexHTTPClient(mailto="test@example.com")
        captured = []

        def fake_get(path, **kwargs):
            captured.append(path)
            return _make_response(200, WORK_FIXTURE)

        with patch.object(client._session, "get", side_effect=fake_get):
            client.get_work("https://doi.org/10.1038/abc")
        assert captured == ["/works/https://doi.org/10.1038/abc"]

    def test_get_work_bare_doi_normalized_to_doi_form(self):
        """A bare DOI (``10.xxxx/…``) is prefixed with ``doi:`` before quoting.

        OpenAlex's ``/works/{id}`` endpoint 404s on a bare DOI; it requires the
        ``doi:`` form (or a full URL). The client owns this id-form contract.
        """
        client = OpenAlexHTTPClient(mailto="test@example.com")
        captured = []

        def fake_get(path, **kwargs):
            captured.append(path)
            return _make_response(200, WORK_FIXTURE)

        with patch.object(client._session, "get", side_effect=fake_get):
            client.get_work("10.1002/glia.24258")
        # doi: prefix added; the ':' and '/' survive quoting (safe=':/').
        assert captured == ["/works/doi:10.1002/glia.24258"]
        assert "%2F" not in captured[0]
        assert "%3A" not in captured[0]

    def test_get_work_doi_form_not_double_prefixed(self):
        """An id already in ``doi:`` form must not get a second ``doi:`` prefix."""
        client = OpenAlexHTTPClient(mailto="test@example.com")
        captured = []

        def fake_get(path, **kwargs):
            captured.append(path)
            return _make_response(200, WORK_FIXTURE)

        with patch.object(client._session, "get", side_effect=fake_get):
            client.get_work("doi:10.1/x")
        assert captured == ["/works/doi:10.1/x"]

    def test_get_work_openalex_id_unchanged(self):
        """A bare ``W…`` id must pass through untouched."""
        client = OpenAlexHTTPClient(mailto="test@example.com")
        captured = []

        def fake_get(path, **kwargs):
            captured.append(path)
            return _make_response(200, WORK_FIXTURE)

        with patch.object(client._session, "get", side_effect=fake_get):
            client.get_work("W123")
        assert captured == ["/works/W123"]

    def test_get_funder_bare_doi_normalized_to_doi_form(self):
        """A bare funder DOI (Crossref Funder Registry) gets the ``doi:`` prefix."""
        client = OpenAlexHTTPClient(mailto="test@example.com")
        captured = []

        def fake_get(path, **kwargs):
            captured.append(path)
            return _make_response(200, FUNDER_FIXTURE)

        with patch.object(client._session, "get", side_effect=fake_get):
            client.get_funder("10.13039/501100001691")
        assert captured == ["/funders/doi:10.13039/501100001691"]


# ---------------------------------------------------------------------------
# 2. mailto / polite-pool behaviour
# ---------------------------------------------------------------------------

class TestMailto:
    def test_mailto_sent_as_query_param_and_in_ua(self):
        client = OpenAlexHTTPClient(mailto="researcher@uni.edu")
        captured = []

        def fake_get(path, **kwargs):
            captured.append(kwargs)
            return _make_response(200, WORK_FIXTURE)

        with patch.object(client._session, "get", side_effect=fake_get):
            client.get_work("W1")

        assert captured
        params = captured[0].get("params", {})
        assert params.get("mailto") == "researcher@uni.edu"
        ua = client._session.headers.get("User-Agent", "")
        assert "researcher@uni.edu" in ua

    def test_no_mailto_plain_ua_no_param_and_warns_once(self, monkeypatch, caplog):
        import open_pulse_crawler.platforms.openalex_adapter.client as mod
        mod._no_mailto_warned = False

        monkeypatch.setattr(
            "open_pulse_crawler.platforms.openalex_adapter.client.config.resolve_openalex_mailto",
            lambda: None,
        )

        captured = []

        def fake_get(path, **kwargs):
            captured.append(kwargs)
            return _make_response(200, WORK_FIXTURE)

        with caplog.at_level(
            logging.WARNING,
            logger="open_pulse_crawler.platforms.openalex_adapter.client",
        ):
            client = OpenAlexHTTPClient(mailto=None)
            OpenAlexHTTPClient(mailto=None)  # second construction: no extra warning

        ua = client._session.headers.get("User-Agent", "")
        assert "@" not in ua
        assert "OpenPulseCrawler" in ua

        with patch.object(client._session, "get", side_effect=fake_get):
            client.get_work("W1")
        assert "mailto" not in captured[0].get("params", {})

        warns = [r.message for r in caplog.records if r.levelno == logging.WARNING]
        mailto_warns = [m for m in warns if "OPENALEX_MAILTO" in m]
        assert len(mailto_warns) == 1, mailto_warns


# ---------------------------------------------------------------------------
# 3. 429 retry
# ---------------------------------------------------------------------------

class TestRetry429:
    def test_429_then_200(self, monkeypatch):
        client = OpenAlexHTTPClient(mailto="test@example.com")
        sleeps = []
        monkeypatch.setattr(
            "open_pulse_crawler.platforms.openalex_adapter.client.time.sleep",
            lambda s: sleeps.append(s),
        )
        responses = [
            _make_response(429, headers={"Retry-After": "0"}),
            _make_response(200, WORK_FIXTURE),
        ]
        with patch.object(client._session, "get", side_effect=responses):
            work = client.get_work("W1")
        assert work is not None
        assert sleeps == [0.0]

    def test_429_caps_at_60(self, monkeypatch):
        client = OpenAlexHTTPClient(mailto="test@example.com")
        sleeps = []
        monkeypatch.setattr(
            "open_pulse_crawler.platforms.openalex_adapter.client.time.sleep",
            lambda s: sleeps.append(s),
        )
        responses = [
            _make_response(429, headers={"Retry-After": "9999"}),
            _make_response(200, WORK_FIXTURE),
        ]
        with patch.object(client._session, "get", side_effect=responses):
            client.get_work("W1")
        assert sleeps == [60.0]

    def test_429_twice_raises(self, monkeypatch):
        client = OpenAlexHTTPClient(mailto="test@example.com")
        monkeypatch.setattr(
            "open_pulse_crawler.platforms.openalex_adapter.client.time.sleep",
            lambda s: None,
        )
        responses = [
            _make_response(429, headers={"Retry-After": "0"}),
            _make_response(429),
        ]
        with patch.object(client._session, "get", side_effect=responses):
            with pytest.raises(httpx.HTTPStatusError):
                client.get_work("W1")

    def test_429_default_sleep_when_no_header(self, monkeypatch):
        client = OpenAlexHTTPClient(mailto="test@example.com")
        sleeps = []
        monkeypatch.setattr(
            "open_pulse_crawler.platforms.openalex_adapter.client.time.sleep",
            lambda s: sleeps.append(s),
        )
        responses = [
            _make_response(429, headers={}),
            _make_response(200, WORK_FIXTURE),
        ]
        with patch.object(client._session, "get", side_effect=responses):
            client.get_work("W1")
        assert sleeps == [5.0]


# ---------------------------------------------------------------------------
# 4. iter_citing_works — cursor pagination + cap
# ---------------------------------------------------------------------------

class TestIterCitingWorks:
    def _page(self, results, next_cursor):
        return {"results": results, "meta": {"next_cursor": next_cursor}}

    def test_paginates_two_cursor_pages(self):
        client = OpenAlexHTTPClient(mailto="test@example.com")
        page1 = self._page(
            [{"id": "https://openalex.org/W11", "doi": None},
             {"id": "https://openalex.org/W12", "doi": None}],
            next_cursor="CURSOR2",
        )
        page2 = self._page(
            [{"id": "https://openalex.org/W13", "doi": None}],
            next_cursor=None,
        )
        captured = []

        def fake_get(path, **kwargs):
            captured.append(kwargs.get("params", {}))
            return _make_response(200, page1 if len(captured) == 1 else page2)

        with patch.object(client._session, "get", side_effect=fake_get):
            out = list(client.iter_citing_works("https://openalex.org/W2741809807", cap=None))

        assert [w["id"] for w in out] == [
            "https://openalex.org/W11",
            "https://openalex.org/W12",
            "https://openalex.org/W13",
        ]
        # First page uses cursor=*; second uses the returned next_cursor.
        assert captured[0]["cursor"] == "*"
        assert captured[1]["cursor"] == "CURSOR2"
        # Bare W-id used in the cites filter.
        assert captured[0]["filter"] == "cites:W2741809807"

    def test_respects_cap(self):
        client = OpenAlexHTTPClient(mailto="test@example.com")
        page1 = self._page(
            [{"id": f"https://openalex.org/W{i}", "doi": None} for i in range(200)],
            next_cursor="CURSOR2",
        )
        page2 = self._page(
            [{"id": f"https://openalex.org/W{i}", "doi": None} for i in range(200, 400)],
            next_cursor=None,
        )

        def fake_get(path, **kwargs):
            cur = kwargs.get("params", {}).get("cursor")
            return _make_response(200, page1 if cur == "*" else page2)

        with patch.object(client._session, "get", side_effect=fake_get) as mock_get:
            out = list(client.iter_citing_works("W2741809807", cap=3))
        assert len(out) == 3
        assert mock_get.call_count == 1

    def test_stuck_cursor_terminates(self, caplog):
        """If next_cursor echoes the same value, the iterator must break (not loop)."""
        client = OpenAlexHTTPClient(mailto="test@example.com")
        # Page whose meta.next_cursor is identical to the initial cursor ("*").
        page1 = self._page(
            [{"id": "https://openalex.org/W1", "doi": None}],
            next_cursor="*",
        )

        call_count = []

        def fake_get(path, **kwargs):
            call_count.append(1)
            return _make_response(200, page1)

        with caplog.at_level(
            logging.WARNING,
            logger="open_pulse_crawler.platforms.openalex_adapter.client",
        ):
            with patch.object(client._session, "get", side_effect=fake_get):
                out = list(client.iter_citing_works("W1", cap=None))

        # Only the first page's results are returned — iterator did not loop.
        assert [w["id"] for w in out] == ["https://openalex.org/W1"]
        assert len(call_count) == 1
        # A warning about the stuck cursor must have been emitted.
        warns = [r.message for r in caplog.records if r.levelno == logging.WARNING]
        assert any("stuck" in m or "did not advance" in m for m in warns)


# ---------------------------------------------------------------------------
# 5. iter_works_by_entity
# ---------------------------------------------------------------------------

class TestIterWorksByEntity:
    def test_filter_key_used(self):
        client = OpenAlexHTTPClient(mailto="test@example.com")
        page = {"results": [{"id": "https://openalex.org/W1", "doi": None}],
                "meta": {"next_cursor": None}}
        captured = []

        def fake_get(path, **kwargs):
            captured.append(kwargs.get("params", {}))
            return _make_response(200, page)

        with patch.object(client._session, "get", side_effect=fake_get):
            out = list(client.iter_works_by_entity("author.id", "A123", cap=None))
        assert len(out) == 1
        assert captured[0]["filter"] == "author.id:A123"


# ---------------------------------------------------------------------------
# 6. resolve_ids_to_canonical
# ---------------------------------------------------------------------------

class TestResolveIdsToCanonical:
    def test_doi_and_unresolved_fallback(self):
        client = OpenAlexHTTPClient(mailto="test@example.com")
        w1_url = "https://openalex.org/W1"
        w2_url = "https://openalex.org/W2"
        body = {
            "results": [
                {"id": "https://openalex.org/W1",
                 "doi": "https://doi.org/10.1/A"},
                {"id": "https://openalex.org/W2", "doi": None},
            ],
            "meta": {"next_cursor": None},
        }
        with patch.object(client._session, "get",
                          return_value=_make_response(200, body)):
            out = client.resolve_ids_to_canonical([w1_url, w2_url])
        assert out == {
            w1_url: "https://doi.org/10.1/a",
            w2_url: "https://openalex.org/W2",
        }

    def test_input_not_returned_maps_to_own_url(self):
        client = OpenAlexHTTPClient(mailto="test@example.com")
        w1_url = "https://openalex.org/W1"
        w2_url = "https://openalex.org/W2"
        # API returns only W1; W2 is missing entirely.
        body = {
            "results": [
                {"id": "https://openalex.org/W1", "doi": "https://doi.org/10.1/A"},
            ],
            "meta": {"next_cursor": None},
        }
        with patch.object(client._session, "get",
                          return_value=_make_response(200, body)):
            out = client.resolve_ids_to_canonical([w1_url, w2_url])
        assert out[w2_url] == "https://openalex.org/W2"

    def test_batches_in_groups_of_50(self):
        client = OpenAlexHTTPClient(mailto="test@example.com")
        urls = [f"https://openalex.org/W{i}" for i in range(120)]
        captured = []

        def fake_get(path, **kwargs):
            captured.append(kwargs.get("params", {}))
            return _make_response(200, {"results": [], "meta": {"next_cursor": None}})

        with patch.object(client._session, "get", side_effect=fake_get):
            out = client.resolve_ids_to_canonical(urls)
        # 120 ids → ceil(120/50) = 3 batched requests.
        assert len(captured) == 3
        # Each batch filter uses ids.openalex with pipe-joined bare W-ids.
        assert captured[0]["filter"].startswith("ids.openalex:W")
        assert "|" in captured[0]["filter"]
        # All inputs resolve (to own urls here since results empty).
        assert len(out) == 120
        assert out[urls[0]] == urls[0]

    def test_http_doi_prefix_resolved(self):
        """An http:// doi in the API response must yield a canonical https:// URL."""
        client = OpenAlexHTTPClient(mailto="test@example.com")
        w1_url = "https://openalex.org/W1"
        body = {
            "results": [
                {"id": "https://openalex.org/W1",
                 "doi": "http://doi.org/10.1/B"},
            ],
            "meta": {"next_cursor": None},
        }
        with patch.object(client._session, "get",
                          return_value=_make_response(200, body)):
            out = client.resolve_ids_to_canonical([w1_url])
        # Must be https://, lower-cased, no doubled prefix.
        assert out[w1_url] == "https://doi.org/10.1/b"

    def test_empty_input(self):
        client = OpenAlexHTTPClient(mailto="test@example.com")
        with patch.object(client._session, "get") as g:
            out = client.resolve_ids_to_canonical([])
        assert out == {}
        g.assert_not_called()


# ---------------------------------------------------------------------------
# 7. other entity getters
# ---------------------------------------------------------------------------

class TestOtherGetters:
    @pytest.mark.parametrize(
        "method,arg,fixture,path",
        [
            ("get_author", "A5023888391", AUTHOR_FIXTURE, "/authors/A5023888391"),
            ("get_institution", "I4210090411", INSTITUTION_FIXTURE, "/institutions/I4210090411"),
            ("get_source", "S137773608", SOURCE_FIXTURE, "/sources/S137773608"),
            ("get_funder", "F4320306076", FUNDER_FIXTURE, "/funders/F4320306076"),
        ],
    )
    def test_getter_parses_fixture(self, method, arg, fixture, path):
        client = OpenAlexHTTPClient(mailto="test@example.com")
        captured = []

        def fake_get(p, **kwargs):
            captured.append(p)
            return _make_response(200, fixture)

        with patch.object(client._session, "get", side_effect=fake_get):
            result = getattr(client, method)(arg)
        assert result == fixture
        assert captured == [path]

    @pytest.mark.parametrize(
        "method,arg",
        [
            ("get_author", "A_missing"),
            ("get_institution", "I_missing"),
            ("get_source", "S_missing"),
            ("get_funder", "F_missing"),
        ],
    )
    def test_getter_404_returns_none(self, method, arg):
        client = OpenAlexHTTPClient(mailto="test@example.com")
        with patch.object(client._session, "get",
                          return_value=_make_response(404)):
            assert getattr(client, method)(arg) is None

    def test_getter_id_path_quoted(self):
        """orcid:/ror: id forms keep their colon and slashes."""
        client = OpenAlexHTTPClient(mailto="test@example.com")
        captured = []

        def fake_get(p, **kwargs):
            captured.append(p)
            return _make_response(200, AUTHOR_FIXTURE)

        with patch.object(client._session, "get", side_effect=fake_get):
            client.get_author("orcid:0000-0001-6169-6851")
        assert captured == ["/authors/orcid:0000-0001-6169-6851"]

    def test_get_author_bare_orcid_normalized_to_orcid_form(self):
        """A bare ORCID is prefixed with ``orcid:`` before quoting.

        OpenAlex's ``/authors/{id}`` endpoint does not resolve a bare ORCID; it
        requires the ``orcid:`` form (or a full URL). The client owns this
        id-form contract.
        """
        client = OpenAlexHTTPClient(mailto="test@example.com")
        captured = []

        def fake_get(p, **kwargs):
            captured.append(p)
            return _make_response(200, AUTHOR_FIXTURE)

        with patch.object(client._session, "get", side_effect=fake_get):
            client.get_author("0000-0002-3336-0163")
        assert captured == ["/authors/orcid:0000-0002-3336-0163"]
        assert "%2F" not in captured[0]
        assert "%3A" not in captured[0]

    def test_get_institution_bare_ror_normalized_to_ror_form(self):
        """A bare ROR is prefixed with ``ror:`` before quoting.

        OpenAlex's ``/institutions/{id}`` endpoint does not resolve a bare ROR;
        it requires the ``ror:`` form (or a full URL).
        """
        client = OpenAlexHTTPClient(mailto="test@example.com")
        captured = []

        def fake_get(p, **kwargs):
            captured.append(p)
            return _make_response(200, INSTITUTION_FIXTURE)

        with patch.object(client._session, "get", side_effect=fake_get):
            client.get_institution("02s376052")
        assert captured == ["/institutions/ror:02s376052"]
        assert "%2F" not in captured[0]
        assert "%3A" not in captured[0]

    def test_get_author_orcid_form_not_double_prefixed(self):
        """An id already in ``orcid:`` form must not get a second prefix."""
        client = OpenAlexHTTPClient(mailto="test@example.com")
        captured = []

        def fake_get(p, **kwargs):
            captured.append(p)
            return _make_response(200, AUTHOR_FIXTURE)

        with patch.object(client._session, "get", side_effect=fake_get):
            client.get_author("orcid:0000-0002-3336-0163")
        assert captured == ["/authors/orcid:0000-0002-3336-0163"]

    def test_get_institution_ror_form_not_double_prefixed(self):
        """An id already in ``ror:`` form must not get a second prefix."""
        client = OpenAlexHTTPClient(mailto="test@example.com")
        captured = []

        def fake_get(p, **kwargs):
            captured.append(p)
            return _make_response(200, INSTITUTION_FIXTURE)

        with patch.object(client._session, "get", side_effect=fake_get):
            client.get_institution("ror:02s376052")
        assert captured == ["/institutions/ror:02s376052"]

    def test_get_author_openalex_id_unchanged(self):
        """A bare ``A…`` id must pass through untouched."""
        client = OpenAlexHTTPClient(mailto="test@example.com")
        captured = []

        def fake_get(p, **kwargs):
            captured.append(p)
            return _make_response(200, AUTHOR_FIXTURE)

        with patch.object(client._session, "get", side_effect=fake_get):
            client.get_author("A123")
        assert captured == ["/authors/A123"]

    def test_get_institution_openalex_id_unchanged(self):
        """A bare ``I…`` id must pass through untouched."""
        client = OpenAlexHTTPClient(mailto="test@example.com")
        captured = []

        def fake_get(p, **kwargs):
            captured.append(p)
            return _make_response(200, INSTITUTION_FIXTURE)

        with patch.object(client._session, "get", side_effect=fake_get):
            client.get_institution("I123")
        assert captured == ["/institutions/I123"]


# ---------------------------------------------------------------------------
# 8. resource management
# ---------------------------------------------------------------------------

class TestClose:
    def test_context_manager_closes_session(self):
        with OpenAlexHTTPClient(mailto="test@example.com") as client:
            assert not client._session.is_closed
        assert client._session.is_closed

    def test_close_idempotent(self):
        client = OpenAlexHTTPClient(mailto="test@example.com")
        client.close()
        client.close()
        assert client._session.is_closed
