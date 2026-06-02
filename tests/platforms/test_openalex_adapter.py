"""Tests for the OpenAlex PlatformAdapter (normalize_uri / classify / fetch + builders)."""
import pytest

from open_pulse_crawler.models import (
    OpenAlexWork, OpenAlexAuthor, OpenAlexInstitution, OpenAlexSource,
    OpenAlexFunder,
)
from open_pulse_crawler.node_id import NodeKind
from open_pulse_crawler.platforms.openalex_adapter.adapter import OpenAlexAdapter


# --- fakes ----------------------------------------------------------


class FakeClient:
    """A small stand-in for OpenAlexHTTPClient: prebuilt dicts keyed by id."""

    def __init__(self, works=None, authors=None, institutions=None,
                 sources=None, funders=None):
        self.works = works or {}
        self.authors = authors or {}
        self.institutions = institutions or {}
        self.sources = sources or {}
        self.funders = funders or {}
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
        "grants": [{"funder": "https://openalex.org/F1", "funder_display_name": "NSF"}],
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
    assert node.funded_by == []
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
    assert node.url == "https://doi.org/10.13039/501100000780"
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


def test_fetch_institution_miss_delegates_to_fallback():
    fb = FakeFallback()
    adapter = OpenAlexAdapter(client=FakeClient(), fallback_adapter=fb)
    result = adapter.fetch("https://ror.org/02s376052")
    assert result is FakeFallback.SENTINEL


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


# --- expand placeholder + rate limit --------------------------------


def test_expand_is_empty_placeholder(adapter):
    assert list(adapter.expand(object(), None)) == []


def test_rate_limit_state(adapter):
    info = adapter.rate_limit_state()
    assert info.remaining == 1000
    assert info.limit == 2000
    assert info.reset_at is None
