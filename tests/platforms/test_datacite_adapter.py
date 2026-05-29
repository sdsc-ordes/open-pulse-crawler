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
