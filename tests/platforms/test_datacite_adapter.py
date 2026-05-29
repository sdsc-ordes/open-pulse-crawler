"""Tests for the DataCite PlatformAdapter."""
from unittest.mock import MagicMock
import pytest

from open_pulse_crawler.models import (
    DataCiteWork, DataCiteOrganization, DataCitePerson, DataCiteClient,
)
from open_pulse_crawler.node_id import NodeKind
from open_pulse_crawler.platforms.datacite_adapter.adapter import DataCiteAdapter


@pytest.fixture
def adapter():
    client = MagicMock()
    return DataCiteAdapter(client=client, instance_host="api.datacite.org")


# --- classify -------------------------------------------------------

def test_classify_doi_url(adapter):
    assert adapter.classify("https://doi.org/10.6084/m9.figshare.99") == NodeKind.REPO


def test_classify_ror_url(adapter):
    assert adapter.classify("https://ror.org/02s376052") == NodeKind.ORG


def test_classify_orcid_url(adapter):
    assert adapter.classify("https://orcid.org/0000-0002-1825-0097") == NodeKind.USER


def test_classify_client_url(adapter):
    assert adapter.classify("https://commons.datacite.org/repositories/cern.zenodo") == NodeKind.ORG


def test_classify_unknown_path(adapter):
    assert adapter.classify("https://api.datacite.org/somethingelse") is None


# --- normalize_uri --------------------------------------------------

def test_normalize_doi_url_with_owned_zenodo_prefix_rewrites(adapter):
    """A 10.5281/zenodo.X DOI URL gets rewritten to its Zenodo URL,
    so BFS dispatches to the Zenodo adapter — DataCite never sees it."""
    assert adapter.normalize_uri("https://doi.org/10.5281/zenodo.42") == \
        "https://zenodo.org/records/42"


def test_normalize_doi_url_with_unowned_prefix_unchanged(adapter):
    assert adapter.normalize_uri("https://doi.org/10.6084/m9.figshare.99") == \
        "https://doi.org/10.6084/m9.figshare.99"


def test_normalize_api_datacite_dois_rewrites_to_doi_org(adapter):
    assert adapter.normalize_uri("https://api.datacite.org/dois/10.6084/m9.figshare.99") == \
        "https://doi.org/10.6084/m9.figshare.99"


def test_normalize_commons_doi_alias(adapter):
    assert adapter.normalize_uri("https://commons.datacite.org/doi.org/10.6084/m9.figshare.99") == \
        "https://doi.org/10.6084/m9.figshare.99"


def test_normalize_ror_strips_trailing_slash(adapter):
    assert adapter.normalize_uri("https://ror.org/02s376052/") == \
        "https://ror.org/02s376052"


def test_normalize_commons_ror_alias(adapter):
    assert adapter.normalize_uri("https://commons.datacite.org/ror.org/02s376052") == \
        "https://ror.org/02s376052"


def test_normalize_orcid_strips_query_and_trailing_slash(adapter):
    assert adapter.normalize_uri("https://orcid.org/0000-0002-1825-0097/?x=1") == \
        "https://orcid.org/0000-0002-1825-0097"


def test_normalize_commons_orcid_alias(adapter):
    assert adapter.normalize_uri("https://commons.datacite.org/orcid.org/0000-0002-1825-0097") == \
        "https://orcid.org/0000-0002-1825-0097"


def test_normalize_api_clients_rewrites_to_commons(adapter):
    assert adapter.normalize_uri("https://api.datacite.org/clients/cern.zenodo") == \
        "https://commons.datacite.org/repositories/cern.zenodo"


def test_normalize_commons_repositories_canonical(adapter):
    assert adapter.normalize_uri("https://commons.datacite.org/repositories/cern.zenodo") == \
        "https://commons.datacite.org/repositories/cern.zenodo"


# --- fetch ----------------------------------------------------------

def test_fetch_doi_returns_work(adapter):
    adapter._client.get_doi.return_value = {
        "id": "10.6084/m9.figshare.99",
        "type": "dois",
        "attributes": {
            "doi": "10.6084/m9.figshare.99",
            "types": {"resourceTypeGeneral": "Dataset", "resourceType": "Tabular"},
            "titles": [{"title": "A study of X"}],
            "publicationYear": 2024,
            "publisher": "figshare",
            "url": "https://figshare.com/articles/dataset/A_study/99",
            "language": "en",
            "creators": [
                {"name": "Doe, J.",
                 "nameIdentifiers": [{"nameIdentifier": "https://orcid.org/0000-0001-2345-6789",
                                      "nameIdentifierScheme": "ORCID"}],
                 "affiliation": [
                     {"name": "EPFL",
                      "affiliationIdentifier": "https://ror.org/02s376052",
                      "affiliationIdentifierScheme": "ROR"},
                 ]},
            ],
            "subjects": [{"subject": "kw1"}, {"subject": "kw2"}],
            "descriptions": [{"description": "An abstract.", "descriptionType": "Abstract"}],
            "container": {"title": "Nature"},
            "relatedIdentifiers": [
                {"relationType": "IsSupplementTo",
                 "relatedIdentifierType": "URL",
                 "relatedIdentifier": "https://github.com/foo/bar"},
            ],
        },
        "relationships": {"client": {"data": {"id": "figshare.ars"}}},
    }
    node = adapter.fetch("https://doi.org/10.6084/m9.figshare.99")
    assert isinstance(node, DataCiteWork)
    assert node.doi == "10.6084/m9.figshare.99"
    assert node.resource_type == "Dataset"
    assert node.resource_type_detail == "Tabular"
    assert node.title == "A study of X"
    assert node.publication_year == 2024
    assert node.publisher == "figshare"
    assert node.client_id == "figshare.ars"
    assert node.language == "en"
    assert node.registered_url == "https://figshare.com/articles/dataset/A_study/99"
    assert node.subjects == ["kw1", "kw2"]
    assert node.abstract == "An abstract."
    assert node.container_title == "Nature"
    # creator with ORCID + ROR is extracted to typed creators list
    assert len(node.creators) == 1
    cre = node.creators[0]
    assert cre["name"] == "Doe, J."
    assert cre["orcid"] == "0000-0001-2345-6789"
    assert cre["affiliations"][0]["ror"] == "02s376052"
    assert cre["affiliations"][0]["name"] == "EPFL"
    assert cre["affiliations"][0]["scheme"] == "ROR"
    # deduped affiliations + relations
    assert any(a["ror"] == "02s376052" for a in node.affiliations)
    assert node.relations[0]["relation_type"] == "IsSupplementTo"
    assert node.relations[0]["target"] == "https://github.com/foo/bar"


def test_fetch_doi_404_returns_none(adapter):
    adapter._client.get_doi.return_value = None
    assert adapter.fetch("https://doi.org/10.6084/m9.figshare.missing") is None


def test_fetch_ror_returns_bare_organization(adapter):
    """ROR fetch makes NO network call — the node is constructed from URL alone."""
    node = adapter.fetch("https://ror.org/02s376052")
    assert isinstance(node, DataCiteOrganization)
    assert node.ror_id == "02s376052"
    assert node.ror_url == "https://ror.org/02s376052"
    assert node.name == ""  # bare anchor — name comes from creator entries elsewhere
    adapter._client.get_doi.assert_not_called()
    adapter._client.get_client.assert_not_called()


def test_fetch_orcid_returns_bare_person(adapter):
    """ORCID fetch makes NO network call — the node is constructed from URL alone."""
    node = adapter.fetch("https://orcid.org/0000-0002-1825-0097")
    assert isinstance(node, DataCitePerson)
    assert node.orcid == "0000-0002-1825-0097"
    assert node.orcid_url == "https://orcid.org/0000-0002-1825-0097"
    assert node.login == "0000-0002-1825-0097"


def test_fetch_client_returns_datacite_client(adapter):
    adapter._client.get_client.return_value = {
        "id": "cern.zenodo",
        "type": "clients",
        "attributes": {
            "name": "Zenodo",
            "alternateName": "Research. Shared",
            "symbol": "CERN.ZENODO",
            "year": 2013,
            "clientType": "repository",
            "repositoryType": ["disciplinary"],
            "description": "ZENODO builds and operates …",
            "url": "https://zenodo.org/",
            "domains": "openaire.cern.ch,zenodo.org",
            "re3data": "https://doi.org/10.17616/R3QP53",
            "isActive": True,
            "language": ["en"],
        },
    }
    adapter._client.get_client_prefixes.return_value = ["10.5281", "10.5072"]
    node = adapter.fetch("https://commons.datacite.org/repositories/cern.zenodo")
    assert isinstance(node, DataCiteClient)
    assert node.client_id == "cern.zenodo"
    assert node.repository_name == "Zenodo"
    assert node.alternate_name == "Research. Shared"
    assert node.client_type == "repository"
    assert node.repository_type == ["disciplinary"]
    assert node.repository_url == "https://zenodo.org/"
    assert node.domains == ["openaire.cern.ch", "zenodo.org"]
    assert node.re3data_doi == "https://doi.org/10.17616/R3QP53"
    assert node.year_registered == 2013
    assert node.is_active is True
    assert node.doi_prefixes == ["10.5281", "10.5072"]


def test_fetch_client_404_returns_none(adapter):
    adapter._client.get_client.return_value = None
    assert adapter.fetch("https://commons.datacite.org/repositories/missing") is None


def test_fetch_unknown_url_form_returns_none(adapter):
    assert adapter.fetch("https://api.datacite.org/somethingelse") is None
