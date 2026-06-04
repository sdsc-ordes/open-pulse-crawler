"""Tests for the OpenAlex PlatformAdapter (normalize_uri / classify / fetch + builders)."""
import logging

import pytest

from open_pulse_crawler.models import (
    OpenAlexWork, OpenAlexAuthor, OpenAlexInstitution, OpenAlexSource,
    OpenAlexFunder,
)
from open_pulse_crawler.node_id import NodeKind
from open_pulse_crawler.platforms.base import ExpandOpts
from open_pulse_crawler.platforms.openalex_adapter.adapter import OpenAlexAdapter


# --- fakes ----------------------------------------------------------


class FakeClient:
    """A small stand-in for OpenAlexHTTPClient: prebuilt dicts keyed by id."""

    def __init__(self, works=None, authors=None, institutions=None,
                 sources=None, funders=None, resolved=None,
                 citing=None, entity_works=None):
        self.works = works or {}
        self.authors = authors or {}
        self.institutions = institutions or {}
        self.sources = sources or {}
        self.funders = funders or {}
        # expand-fakes
        self.resolved = resolved or {}
        self.citing = citing or []
        self.entity_works = entity_works or []
        self.calls = []

    def get_work(self, id):
        self.calls.append(("get_work", id))
        return self.works.get(id)

    def get_author(self, id):
        self.calls.append(("get_author", id))
        return self.authors.get(id)

    def get_institution(self, id):
        self.calls.append(("get_institution", id))
        return self.institutions.get(id)

    def get_source(self, id):
        self.calls.append(("get_source", id))
        return self.sources.get(id)

    def get_funder(self, id):
        self.calls.append(("get_funder", id))
        return self.funders.get(id)

    # ---- expand-side methods ----
    def resolve_ids_to_canonical(self, urls):
        self.calls.append(("resolve_ids_to_canonical", list(urls)))
        # default any unresolved input to itself, then override from canned map.
        out = {u: u for u in urls}
        out.update({u: self.resolved[u] for u in urls if u in self.resolved})
        return out

    def iter_citing_works(self, work_openalex_id, cap):
        self.calls.append(("iter_citing_works", work_openalex_id, cap))
        items = self.citing
        if cap is not None:
            items = items[:cap]
        return iter(items)

    def iter_works_by_entity(self, filter_key, entity_id, cap):
        self.calls.append(("iter_works_by_entity", filter_key, entity_id, cap))
        items = self.entity_works
        if cap is not None:
            items = items[:cap]
        return iter(items)


class FakeFallback:
    """A stub fallback adapter whose fetch returns a sentinel."""

    SENTINEL = object()

    def __init__(self):
        self.fetched = []

    def fetch(self, uri):
        self.fetched.append(uri)
        return self.SENTINEL


@pytest.fixture
def adapter():
    return OpenAlexAdapter(client=FakeClient())


# --- normalize_uri --------------------------------------------------


def test_normalize_doi_work_lowercased(adapter):
    assert adapter.normalize_uri("https://doi.org/10.1002/GLIA.X") == \
        "https://doi.org/10.1002/glia.x"


def test_normalize_doi_funder(adapter):
    assert adapter.normalize_uri("https://doi.org/10.13039/501100000780") == \
        "https://doi.org/10.13039/501100000780"


def test_normalize_orcid(adapter):
    assert adapter.normalize_uri("https://orcid.org/0000-0002-1825-0097") == \
        "https://orcid.org/0000-0002-1825-0097"


def test_normalize_ror(adapter):
    assert adapter.normalize_uri("https://ror.org/02s376052") == \
        "https://ror.org/02s376052"


def test_normalize_openalex_work(adapter):
    assert adapter.normalize_uri("https://openalex.org/W1") == \
        "https://openalex.org/W1"


def test_normalize_api_works(adapter):
    assert adapter.normalize_uri("https://api.openalex.org/works/W1") == \
        "https://openalex.org/W1"


def test_normalize_api_authors(adapter):
    assert adapter.normalize_uri("https://api.openalex.org/authors/A99") == \
        "https://openalex.org/A99"


def test_normalize_api_funders(adapter):
    assert adapter.normalize_uri("https://api.openalex.org/funders/F42") == \
        "https://openalex.org/F42"


def test_normalize_unknown_fallback(adapter):
    # Unknown host/path is canonicalized so the BFS can drop it.
    out = adapter.normalize_uri("https://example.com/foo/bar")
    assert out == "https://example.com/foo/bar"


# --- classify -------------------------------------------------------


def test_classify_funder_doi_is_org(adapter):
    assert adapter.classify("https://doi.org/10.13039/501100000780") == NodeKind.ORG


def test_classify_work_doi_is_repo(adapter):
    assert adapter.classify("https://doi.org/10.1002/glia.x") == NodeKind.REPO


def test_classify_orcid_is_user(adapter):
    assert adapter.classify("https://orcid.org/0000-0002-1825-0097") == NodeKind.USER


def test_classify_ror_is_org(adapter):
    assert adapter.classify("https://ror.org/02s376052") == NodeKind.ORG


def test_classify_openalex_source_is_org(adapter):
    assert adapter.classify("https://openalex.org/S12345") == NodeKind.ORG


def test_classify_openalex_work_is_repo(adapter):
    assert adapter.classify("https://openalex.org/W1") == NodeKind.REPO


def test_classify_openalex_author_is_user(adapter):
    assert adapter.classify("https://openalex.org/A1") == NodeKind.USER


def test_classify_openalex_institution_is_org(adapter):
    assert adapter.classify("https://openalex.org/I1") == NodeKind.ORG


def test_classify_openalex_funder_is_org(adapter):
    assert adapter.classify("https://openalex.org/F1") == NodeKind.ORG


def test_classify_unknown_is_none(adapter):
    assert adapter.classify("https://example.com/foo") is None


# --- fetch: builders ------------------------------------------------


def test_fetch_work_builds_openalex_work():
    raw = {
        "id": "https://openalex.org/W2741809807",
        "doi": "https://doi.org/10.1002/GLIA.X",
        "title": "A study",
        "publication_year": 2018,
        "type": "article",
        "cited_by_count": 42,
        "open_access": {"is_oa": True},
        "referenced_works": [
            "https://openalex.org/W111",
            "https://openalex.org/W222",
        ],
        "authorships": [
            {
                "author": {
                    "id": "https://openalex.org/A55",
                    "orcid": "https://orcid.org/0000-0002-1825-0097",
                    "display_name": "Jane Doe",
                },
                "institutions": [
                    {
                        "id": "https://openalex.org/I77",
                        "ror": "https://ror.org/02s376052",
                        "display_name": "Some Uni",
                        "country_code": "US",
                        "type": "education",
                    },
                ],
            },
        ],
        "primary_location": {
            "source": {
                "id": "https://openalex.org/S99",
                "issn_l": "1234-5678",
                "display_name": "Glia",
            },
        },
        "grants": [
            {"funder": "https://openalex.org/F4320332161", "funder_display_name": "NSF"},
            {"funder": "https://openalex.org/F4320306076", "funder_display_name": "NIH"},
        ],
        "ids": {
            "openalex": "https://openalex.org/W2741809807",
            "doi": "https://doi.org/10.1002/glia.x",
            "mag": "2741809807",
            "pmid": "https://pubmed.ncbi.nlm.nih.gov/12345",
        },
    }
    client = FakeClient(works={"10.1002/glia.x": raw})
    adapter = OpenAlexAdapter(client=client)
    node = adapter.fetch("https://doi.org/10.1002/GLIA.X")

    assert isinstance(node, OpenAlexWork)
    assert node.url == "https://doi.org/10.1002/glia.x"
    assert node.platform == "openalex"
    assert node.doi == "10.1002/glia.x"
    assert node.openalex_id == "W2741809807"
    assert node.title == "A study"
    assert node.publication_year == 2018
    assert node.work_type == "article"
    assert node.cited_by_count == 42
    assert node.is_oa is True
    # references kept as raw W-urls for expand to resolve.
    assert node.references == [
        "https://openalex.org/W111",
        "https://openalex.org/W222",
    ]
    assert node.published_in == "https://openalex.org/S99"
    # creators parsed with bare orcid + bare institution rors.
    assert node.creators == [
        {
            "name": "Jane Doe",
            "orcid": "0000-0002-1825-0097",
            "institutions": ["02s376052"],
        },
    ]
    schemes = {e.scheme: e.value for e in node.external_identifiers}
    assert schemes["openalex"] == "https://openalex.org/W2741809807"
    assert schemes["doi"] == "https://doi.org/10.1002/glia.x"
    assert schemes["mag"] == "2741809807"
    # expand-filled fields stay empty.
    assert node.cited_by == []
    assert node.authored_by == []
    # funded_by is populated from grants in _build_work.
    assert node.funded_by == [
        "https://openalex.org/F4320332161",
        "https://openalex.org/F4320306076",
    ]
    # the bare doi was passed to the client getter.
    assert client.calls == [("get_work", "10.1002/glia.x")]


def test_fetch_work_via_openalex_id():
    raw = {"id": "https://openalex.org/W1", "ids": {"openalex": "https://openalex.org/W1"}}
    client = FakeClient(works={"W1": raw})
    adapter = OpenAlexAdapter(client=client)
    node = adapter.fetch("https://openalex.org/W1")
    assert isinstance(node, OpenAlexWork)
    assert node.url == "https://openalex.org/W1"
    assert node.openalex_id == "W1"
    assert client.calls == [("get_work", "W1")]


def test_fetch_author_builds_openalex_author():
    raw = {
        "id": "https://openalex.org/A55",
        "orcid": "https://orcid.org/0000-0002-1825-0097",
        "display_name": "Jane Doe",
        "affiliations": [
            {"id": "https://openalex.org/I77", "ror": "https://ror.org/02s376052"},
        ],
        "ids": {
            "openalex": "https://openalex.org/A55",
            "orcid": "https://orcid.org/0000-0002-1825-0097",
        },
    }
    client = FakeClient(authors={"0000-0002-1825-0097": raw})
    adapter = OpenAlexAdapter(client=client)
    node = adapter.fetch("https://orcid.org/0000-0002-1825-0097")
    assert isinstance(node, OpenAlexAuthor)
    assert node.url == "https://orcid.org/0000-0002-1825-0097"
    assert node.login == "0000-0002-1825-0097"
    assert node.name == "Jane Doe"
    assert node.orcid == "0000-0002-1825-0097"
    assert node.openalex_id == "A55"
    assert node.affiliations == ["https://ror.org/02s376052"]
    assert node.platform == "openalex"
    schemes = {e.scheme: e.value for e in node.external_identifiers}
    assert schemes["orcid"] == "https://orcid.org/0000-0002-1825-0097"
    assert client.calls == [("get_author", "0000-0002-1825-0097")]


def test_fetch_institution_builds_openalex_institution():
    raw = {
        "id": "https://openalex.org/I77",
        "ror": "https://ror.org/02s376052",
        "display_name": "Some Uni",
        "country_code": "US",
        "type": "education",
        "ids": {
            "openalex": "https://openalex.org/I77",
            "ror": "https://ror.org/02s376052",
        },
    }
    client = FakeClient(institutions={"02s376052": raw})
    adapter = OpenAlexAdapter(client=client)
    node = adapter.fetch("https://ror.org/02s376052")
    assert isinstance(node, OpenAlexInstitution)
    assert node.url == "https://ror.org/02s376052"
    assert node.login == "02s376052"
    assert node.name == "Some Uni"
    assert node.ror_id == "02s376052"
    assert node.openalex_id == "I77"
    assert node.country_code == "US"
    assert node.institution_type == "education"
    assert client.calls == [("get_institution", "02s376052")]


def test_fetch_source_builds_openalex_source():
    raw = {
        "id": "https://openalex.org/S99",
        "issn_l": "1234-5678",
        "issn": ["1234-5678", "8765-4321"],
        "display_name": "Glia",
        "host_organization_name": "Wiley",
        "is_oa": False,
        "type": "journal",
        "ids": {"openalex": "https://openalex.org/S99"},
    }
    client = FakeClient(sources={"S99": raw})
    adapter = OpenAlexAdapter(client=client)
    node = adapter.fetch("https://openalex.org/S99")
    assert isinstance(node, OpenAlexSource)
    assert node.url == "https://openalex.org/S99"
    assert node.login == "S99"
    assert node.name == "Glia"
    assert node.openalex_id == "S99"
    assert node.issn_l == "1234-5678"
    assert node.issns == ["1234-5678", "8765-4321"]
    assert node.host_organization == "Wiley"
    assert node.is_oa is False
    assert client.calls == [("get_source", "S99")]


def test_fetch_funder_via_doi_builds_openalex_funder():
    raw = {
        "id": "https://openalex.org/F1",
        "display_name": "NSF",
        "country_code": "US",
        "ids": {
            "openalex": "https://openalex.org/F1",
            "doi": "https://doi.org/10.13039/501100000780",
            "ror": "https://ror.org/021nxhr62",
        },
    }
    client = FakeClient(funders={"10.13039/501100000780": raw})
    adapter = OpenAlexAdapter(client=client)
    node = adapter.fetch("https://doi.org/10.13039/501100000780")
    assert isinstance(node, OpenAlexFunder)
    # Funders always key on the OpenAlex F URL, even when reached via the
    # registry DOI — the registry DOI stays as the funder_doi field only.
    assert node.url == "https://openalex.org/F1"
    assert node.login == "10.13039/501100000780"
    assert node.name == "NSF"
    assert node.openalex_id == "F1"
    assert node.funder_doi == "10.13039/501100000780"
    assert node.country_code == "US"
    assert client.calls == [("get_funder", "10.13039/501100000780")]


def test_fetch_funder_via_openalex_id():
    raw = {"id": "https://openalex.org/F1", "display_name": "NSF",
           "ids": {"openalex": "https://openalex.org/F1"}}
    client = FakeClient(funders={"F1": raw})
    adapter = OpenAlexAdapter(client=client)
    node = adapter.fetch("https://openalex.org/F1")
    assert isinstance(node, OpenAlexFunder)
    assert node.openalex_id == "F1"
    assert client.calls == [("get_funder", "F1")]


# --- cross-id unification: node.url derived from record, not the URI ----


def test_fetch_work_via_openalex_id_unifies_to_doi():
    """A work reached by its OpenAlex W-id must key on the record's DOI
    (lowercased), unifying with the same work reached by its doi.org URL."""
    raw = {
        "id": "https://openalex.org/W4295510681",
        "doi": "https://doi.org/10.1002/GLIA.24258",
        "title": "A study",
        "ids": {
            "openalex": "https://openalex.org/W4295510681",
            "doi": "https://doi.org/10.1002/glia.24258",
        },
    }
    client = FakeClient(works={"W4295510681": raw})
    adapter = OpenAlexAdapter(client=client)
    node = adapter.fetch("https://openalex.org/W4295510681")
    assert isinstance(node, OpenAlexWork)
    # canonical key is derived from the record's DOI, lowercased — NOT the uri.
    assert node.url == "https://doi.org/10.1002/glia.24258"
    assert node.doi == "10.1002/glia.24258"
    assert node.openalex_id == "W4295510681"


def test_fetch_work_via_openalex_id_no_doi_falls_back_to_openalex():
    """A DOI-less work reached by its OpenAlex W-id keys on the openalex URL."""
    raw = {
        "id": "https://openalex.org/W4295510681",
        "title": "No DOI here",
        "ids": {"openalex": "https://openalex.org/W4295510681"},
    }
    client = FakeClient(works={"W4295510681": raw})
    adapter = OpenAlexAdapter(client=client)
    node = adapter.fetch("https://openalex.org/W4295510681")
    assert node.url == "https://openalex.org/W4295510681"
    assert node.doi == ""


def test_fetch_author_via_openalex_id_unifies_to_orcid():
    """An author reached by its OpenAlex A-id keys on the record's ORCID."""
    raw = {
        "id": "https://openalex.org/A55",
        "orcid": "https://orcid.org/0000-0002-1825-0097",
        "display_name": "Jane Doe",
        "ids": {
            "openalex": "https://openalex.org/A55",
            "orcid": "https://orcid.org/0000-0002-1825-0097",
        },
    }
    client = FakeClient(authors={"A55": raw})
    adapter = OpenAlexAdapter(client=client)
    node = adapter.fetch("https://openalex.org/A55")
    assert node.url == "https://orcid.org/0000-0002-1825-0097"
    assert node.orcid == "0000-0002-1825-0097"
    assert node.openalex_id == "A55"


def test_fetch_institution_via_openalex_id_unifies_to_ror():
    """An institution reached by its OpenAlex I-id keys on the record's ROR."""
    raw = {
        "id": "https://openalex.org/I77",
        "ror": "https://ror.org/02s376052",
        "display_name": "Some Uni",
        "ids": {
            "openalex": "https://openalex.org/I77",
            "ror": "https://ror.org/02s376052",
        },
    }
    client = FakeClient(institutions={"I77": raw})
    adapter = OpenAlexAdapter(client=client)
    node = adapter.fetch("https://openalex.org/I77")
    assert node.url == "https://ror.org/02s376052"
    assert node.ror_id == "02s376052"
    assert node.openalex_id == "I77"


def test_fetch_funder_via_openalex_id_keys_on_openalex_and_keeps_doi():
    """A funder always keys on its OpenAlex F-id (the registry DOI is not a
    fetchable seed), but funder_doi is still populated from the record."""
    raw = {
        "id": "https://openalex.org/F1",
        "display_name": "NSF",
        "country_code": "US",
        "ids": {
            "openalex": "https://openalex.org/F1",
            "doi": "https://doi.org/10.13039/501100000780",
            "ror": "https://ror.org/021nxhr62",
        },
    }
    client = FakeClient(funders={"F1": raw})
    adapter = OpenAlexAdapter(client=client)
    node = adapter.fetch("https://openalex.org/F1")
    assert node.url == "https://openalex.org/F1"
    assert node.funder_doi == "10.13039/501100000780"
    assert node.openalex_id == "F1"


# --- _external_ids: empty-value filtering ---------------------------


def test_external_ids_drops_empty_string_values():
    """An ids block with an empty-string value must not produce an ExternalIdentifier."""
    raw = {
        "id": "https://openalex.org/W1",
        "ids": {
            "openalex": "https://openalex.org/W1",
            "doi": "",          # empty string — must be dropped
            "mag": "2741809807",
        },
    }
    client = FakeClient(works={"W1": raw})
    adapter = OpenAlexAdapter(client=client)
    node = adapter.fetch("https://openalex.org/W1")
    schemes = {e.scheme for e in node.external_identifiers}
    assert "doi" not in schemes, "empty-string doi must not produce an ExternalIdentifier"
    assert "openalex" in schemes
    assert "mag" in schemes


# --- fetch: fallback delegation -------------------------------------


def test_fetch_work_miss_delegates_to_fallback():
    fb = FakeFallback()
    adapter = OpenAlexAdapter(client=FakeClient(), fallback_adapter=fb)
    result = adapter.fetch("https://doi.org/10.1002/glia.x")
    assert result is FakeFallback.SENTINEL
    assert fb.fetched == ["https://doi.org/10.1002/glia.x"]


def test_fetch_author_miss_delegates_to_fallback():
    fb = FakeFallback()
    adapter = OpenAlexAdapter(client=FakeClient(), fallback_adapter=fb)
    result = adapter.fetch("https://orcid.org/0000-0002-1825-0097")
    assert result is FakeFallback.SENTINEL
    assert fb.fetched == ["https://orcid.org/0000-0002-1825-0097"]


def test_fetch_institution_miss_delegates_to_fallback():
    fb = FakeFallback()
    adapter = OpenAlexAdapter(client=FakeClient(), fallback_adapter=fb)
    result = adapter.fetch("https://ror.org/02s376052")
    assert result is FakeFallback.SENTINEL
    assert fb.fetched == ["https://ror.org/02s376052"]


def test_fetch_source_miss_returns_none_no_fallback():
    fb = FakeFallback()
    adapter = OpenAlexAdapter(client=FakeClient(), fallback_adapter=fb)
    assert adapter.fetch("https://openalex.org/S99") is None
    assert fb.fetched == []


def test_fetch_funder_miss_returns_none_no_fallback():
    fb = FakeFallback()
    adapter = OpenAlexAdapter(client=FakeClient(), fallback_adapter=fb)
    assert adapter.fetch("https://doi.org/10.13039/501100000780") is None
    assert fb.fetched == []


def test_fetch_miss_no_fallback_returns_none():
    adapter = OpenAlexAdapter(client=FakeClient(), fallback_adapter=None)
    assert adapter.fetch("https://doi.org/10.1002/glia.x") is None
    assert adapter.fetch("https://orcid.org/0000-0002-1825-0097") is None
    assert adapter.fetch("https://ror.org/02s376052") is None


# --- expand ----------------------------------------------------------


def _edge_tuples(edges):
    return [(e.src, e.kind, e.dst) for e in edges]


def test_expand_unknown_node_emits_nothing(adapter):
    assert list(adapter.expand(object(), ExpandOpts())) == []


def test_expand_work_full():
    node = OpenAlexWork(
        url="https://doi.org/10.1/x",
        full_name="10.1/x",
        platform="openalex",
        doi="10.1/x",
        openalex_id="W1",
        cited_by_count=3,
        references=["https://openalex.org/W111", "https://openalex.org/W222"],
        published_in="https://openalex.org/S99",
        funded_by=["https://openalex.org/F1"],
        creators=[
            {
                "name": "Jane Doe",
                "orcid": "0000-0002-1825-0097",
                "institutions": ["02s376052"],
            },
            {  # no orcid → authored_by + affiliated_with skipped
                "name": "Anon",
                "orcid": "",
                "institutions": ["999999"],
            },
        ],
    )
    client = FakeClient(
        resolved={
            "https://openalex.org/W111": "https://doi.org/10.5/ref1",
            # W222 has no resolved doi → falls back to its own url
        },
        citing=[
            {"id": "https://openalex.org/W333", "doi": "https://doi.org/10.7/CITE"},
            {"id": "https://openalex.org/W444", "doi": None},
        ],
    )
    adapter = OpenAlexAdapter(client=client)
    opts = ExpandOpts(max_citations_per_work=50)
    edges = _edge_tuples(adapter.expand(node, opts))

    # references (outbound), canonical dst from resolver
    assert ("https://doi.org/10.1/x", "references", "https://doi.org/10.5/ref1") in edges
    assert ("https://doi.org/10.1/x", "references", "https://openalex.org/W222") in edges
    # cited_by: citing work with doi → doi.org lowercased; without doi → openalex url
    assert ("https://doi.org/10.1/x", "cited_by", "https://doi.org/10.7/cite") in edges
    assert ("https://doi.org/10.1/x", "cited_by", "https://openalex.org/W444") in edges
    # authored_by (orcid url) only for creator with orcid
    assert ("https://doi.org/10.1/x", "authored_by",
            "https://orcid.org/0000-0002-1825-0097") in edges
    # affiliated_with anchored on the author's orcid url
    assert ("https://orcid.org/0000-0002-1825-0097", "affiliated_with",
            "https://ror.org/02s376052") in edges
    # creator without orcid → no authored_by, no affiliated_with
    assert all(e[2] != "https://ror.org/999999" for e in edges)
    # published_in
    assert ("https://doi.org/10.1/x", "published_in",
            "https://openalex.org/S99") in edges
    # funded_by
    assert ("https://doi.org/10.1/x", "funded_by",
            "https://openalex.org/F1") in edges


def test_expand_work_funded_by_edges_from_grants_fixture():
    """funded_by edges are emitted when the work was built from a grants-bearing API response."""
    raw = {
        "id": "https://openalex.org/W9999",
        "doi": "https://doi.org/10.1/funded",
        "grants": [
            {"funder": "https://openalex.org/F4320332161", "funder_display_name": "NSF"},
            {"funder": "https://openalex.org/F4320306076", "funder_display_name": "NIH"},
        ],
    }
    client = FakeClient(works={"10.1/funded": raw}, resolved={})
    adapter = OpenAlexAdapter(client=client)
    node = adapter.fetch("https://doi.org/10.1/funded")
    assert node.funded_by == [
        "https://openalex.org/F4320332161",
        "https://openalex.org/F4320306076",
    ]
    edges = _edge_tuples(adapter.expand(node, ExpandOpts()))
    assert ("https://doi.org/10.1/funded", "funded_by",
            "https://openalex.org/F4320332161") in edges
    assert ("https://doi.org/10.1/funded", "funded_by",
            "https://openalex.org/F4320306076") in edges


def test_expand_work_cited_by_cap_honored():
    node = OpenAlexWork(
        url="https://openalex.org/W1", full_name="W1", platform="openalex",
        openalex_id="W1", cited_by_count=10,
    )
    client = FakeClient(citing=[
        {"id": f"https://openalex.org/W{i}", "doi": None} for i in range(10)
    ])
    adapter = OpenAlexAdapter(client=client)
    opts = ExpandOpts(max_citations_per_work=2)
    edges = [e for e in adapter.expand(node, opts) if e.kind == "cited_by"]
    assert len(edges) == 2
    # cap was passed down to the client
    assert ("iter_citing_works", "W1", 2) in client.calls


def test_expand_work_cited_by_truncation_logged(caplog):
    node = OpenAlexWork(
        url="https://openalex.org/W1", full_name="W1", platform="openalex",
        openalex_id="W1", cited_by_count=100,
    )
    client = FakeClient(citing=[
        {"id": "https://openalex.org/W2", "doi": None},
    ])
    adapter = OpenAlexAdapter(client=client)
    opts = ExpandOpts(max_citations_per_work=1)
    with caplog.at_level(logging.INFO):
        list(adapter.expand(node, opts))
    assert any("cited_by truncated" in r.message for r in caplog.records)


def test_expand_work_empty_openalex_id_skips_cited_by():
    node = OpenAlexWork(
        url="https://doi.org/10.1/x", full_name="10.1/x", platform="openalex",
        doi="10.1/x", openalex_id="", references=["https://openalex.org/W111"],
    )
    client = FakeClient(
        resolved={"https://openalex.org/W111": "https://doi.org/10.5/ref1"},
        citing=[{"id": "https://openalex.org/W2", "doi": None}],
    )
    adapter = OpenAlexAdapter(client=client)
    edges = _edge_tuples(adapter.expand(node, ExpandOpts()))
    # references still resolved
    assert ("https://doi.org/10.1/x", "references", "https://doi.org/10.5/ref1") in edges
    # cited_by skipped (no openalex_id)
    assert all(e[1] != "cited_by" for e in edges)
    assert all(c[0] != "iter_citing_works" for c in client.calls)


def test_expand_author_emits_authored():
    node = OpenAlexAuthor(
        url="https://orcid.org/0000-0002-1825-0097",
        login="0000-0002-1825-0097", platform="openalex",
        orcid="0000-0002-1825-0097", openalex_id="A55",
    )
    client = FakeClient(entity_works=[
        {"id": "https://openalex.org/W1", "doi": "https://doi.org/10.1/A"},
        {"id": "https://openalex.org/W2", "doi": None},
    ])
    adapter = OpenAlexAdapter(client=client)
    opts = ExpandOpts(max_works_per_entity=25)
    edges = _edge_tuples(adapter.expand(node, opts))
    assert (node.url, "authored", "https://doi.org/10.1/a") in edges
    assert (node.url, "authored", "https://openalex.org/W2") in edges
    assert ("iter_works_by_entity", "author.id", "A55", 25) in client.calls


def test_expand_author_emits_affiliated_with():
    """Author expansion must emit affiliated_with edges anchored on the author's
    url (src == node.url) so the crawler enqueues the institution and
    OpenAlexInstitution nodes finally materialize. These edges are NOT capped."""
    node = OpenAlexAuthor(
        url="https://orcid.org/0000-0002-1825-0097",
        login="0000-0002-1825-0097", platform="openalex",
        orcid="0000-0002-1825-0097", openalex_id="A55",
        affiliations=[
            "https://ror.org/02s376052",
            "https://ror.org/021nxhr62",
            "https://ror.org/02s376052",  # duplicate — must be deduped
        ],
    )
    client = FakeClient(entity_works=[
        {"id": "https://openalex.org/W1", "doi": "https://doi.org/10.1/A"},
        {"id": "https://openalex.org/W2", "doi": None},
    ])
    adapter = OpenAlexAdapter(client=client)
    opts = ExpandOpts(max_works_per_entity=2)
    edges = _edge_tuples(adapter.expand(node, opts))
    # authored edges still present (capped)
    assert (node.url, "authored", "https://doi.org/10.1/a") in edges
    assert (node.url, "authored", "https://openalex.org/W2") in edges
    # affiliated_with edges anchored on the author url (src == node.url)
    assert (node.url, "affiliated_with", "https://ror.org/02s376052") in edges
    assert (node.url, "affiliated_with", "https://ror.org/021nxhr62") in edges
    # dedupe preserves order, no duplicate edge
    aff = [e for e in edges if e[1] == "affiliated_with"]
    assert aff == [
        (node.url, "affiliated_with", "https://ror.org/02s376052"),
        (node.url, "affiliated_with", "https://ror.org/021nxhr62"),
    ]


def test_expand_institution_emits_affiliated_work():
    node = OpenAlexInstitution(
        url="https://ror.org/02s376052", login="02s376052",
        platform="openalex", ror_id="02s376052", openalex_id="I77",
    )
    client = FakeClient(entity_works=[
        {"id": "https://openalex.org/W1", "doi": None},
    ])
    adapter = OpenAlexAdapter(client=client)
    edges = _edge_tuples(adapter.expand(node, ExpandOpts()))
    assert (node.url, "affiliated_work", "https://openalex.org/W1") in edges
    # institutions have no affiliations field — must not emit affiliated_with
    assert all(e[1] != "affiliated_with" for e in edges)
    assert any(c[0] == "iter_works_by_entity" and c[1] == "institutions.id"
               for c in client.calls)


def test_expand_source_and_funder_emit_nothing():
    src = OpenAlexSource(url="https://openalex.org/S99", login="S99",
                         platform="openalex", openalex_id="S99")
    fund = OpenAlexFunder(url="https://openalex.org/F1", login="F1",
                          platform="openalex", openalex_id="F1")
    adapter = OpenAlexAdapter(client=FakeClient())
    assert list(adapter.expand(src, ExpandOpts())) == []
    assert list(adapter.expand(fund, ExpandOpts())) == []


def test_rate_limit_state(adapter):
    info = adapter.rate_limit_state()
    assert info.remaining == 1000
    assert info.limit == 2000
    assert info.reset_at is None
