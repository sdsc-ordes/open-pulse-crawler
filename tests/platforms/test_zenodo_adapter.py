"""Tests for ``ZenodoAdapter`` — classify / normalize_uri / fetch.

Task 6 of the Zenodo adapter plan. ``expand`` is implemented in Task 7
and only stubbed as ``NotImplementedError`` here.
"""
from unittest.mock import MagicMock

import pytest

from open_pulse_crawler.models import (
    ZenodoCommunityModel,
    ZenodoRecordModel,
    ZenodoUserModel,
)
from open_pulse_crawler.node_id import NodeKind
from open_pulse_crawler.platforms.zenodo.adapter import ZenodoAdapter


@pytest.fixture
def adapter():
    client = MagicMock()
    return ZenodoAdapter(client=client, instance_host="zenodo.org")


# --- classify -------------------------------------------------------

def test_classify_record(adapter):
    assert adapter.classify("https://zenodo.org/records/7234562") == NodeKind.REPO


def test_classify_community(adapter):
    assert adapter.classify("https://zenodo.org/communities/sdsc-ordes") == NodeKind.USER_OR_ORG


def test_classify_user(adapter):
    assert adapter.classify("https://zenodo.org/users/12345") == NodeKind.USER_OR_ORG


def test_classify_unknown_path(adapter):
    assert adapter.classify("https://zenodo.org/about") is None


# --- normalize_uri --------------------------------------------------

def test_normalize_canonical_url(adapter):
    assert adapter.normalize_uri("https://zenodo.org/records/7234562") == \
        "https://zenodo.org/records/7234562"


def test_normalize_strips_trailing_slash(adapter):
    assert adapter.normalize_uri("https://zenodo.org/records/7234562/") == \
        "https://zenodo.org/records/7234562"


def test_normalize_legacy_singular_record_path(adapter):
    """Pre-2023 Zenodo URLs used /record/<id> singular; rewrite to /records/<id>."""
    assert adapter.normalize_uri("https://zenodo.org/record/7234562") == \
        "https://zenodo.org/records/7234562"


def test_normalize_prod_doi_url(adapter):
    assert adapter.normalize_uri("https://doi.org/10.5281/zenodo.7234562") == \
        "https://zenodo.org/records/7234562"


def test_normalize_sandbox_doi_url():
    sandbox_adapter = ZenodoAdapter(client=MagicMock(), instance_host="sandbox.zenodo.org")
    assert sandbox_adapter.normalize_uri("https://doi.org/10.5072/zenodo.9999") == \
        "https://sandbox.zenodo.org/records/9999"


def test_normalize_non_zenodo_doi_raises(adapter):
    with pytest.raises(ValueError):
        adapter.normalize_uri("https://doi.org/10.1234/journal.xyz")


# --- fetch ----------------------------------------------------------

def test_fetch_community(adapter):
    adapter._client.get_community.return_value = {
        "id": "sdsc-ordes",
        "metadata": {
            "title": "SDSC ORDES",
            "description": "Open Research Data — ETH Domain",
            "type": {"id": "organization"},
        },
    }
    node = adapter.fetch("https://zenodo.org/communities/sdsc-ordes")
    assert isinstance(node, ZenodoCommunityModel)
    assert node.url == "https://zenodo.org/communities/sdsc-ordes"
    assert node.login == "sdsc-ordes"
    assert node.community_type == "organization"
    assert node.description.startswith("Open Research Data")


def test_fetch_user(adapter):
    adapter._client.get_user.return_value = {
        "id": 12345,
        "username": "alice",
        "profile": {
            "full_name": "Alice Example",
            "affiliations": "EPFL",
            "identifiers": [{"identifier": "0000-0001-2345-6789", "scheme": "orcid"}],
        },
    }
    node = adapter.fetch("https://zenodo.org/users/12345")
    assert isinstance(node, ZenodoUserModel)
    assert node.login == "alice"
    assert node.affiliation == "EPFL"
    assert node.orcid == "0000-0001-2345-6789"


def test_fetch_user_404_returns_none(adapter):
    adapter._client.get_user.return_value = None
    assert adapter.fetch("https://zenodo.org/users/99999") is None


def test_fetch_record_self_concept(adapter):
    """A record whose conceptrecid == its own id is the concept record itself."""
    adapter._client.get_record.return_value = {
        "id": 7234562,
        "conceptrecid": "7234562",
        "doi": "10.5281/zenodo.7234562",
        "conceptdoi": "10.5281/zenodo.7234562",
        "metadata": {
            "title": "Self-concept test",
            "publication_date": "2024-06-30",
            "resource_type": {"id": "software"},
            "license": {"id": "Apache-2.0"},
            "access_right": "open",
            "creators": [{"name": "Doe, J."}],
            "keywords": ["kw"],
        },
    }
    node = adapter.fetch("https://zenodo.org/records/7234562")
    assert isinstance(node, ZenodoRecordModel)
    assert node.url == "https://zenodo.org/records/7234562"
    assert node.doi == "10.5281/zenodo.7234562"
    assert node.concept_doi == "10.5281/zenodo.7234562"  # self-reference
    assert node.resource_type == "software"
    assert node.title == "Self-concept test"
    assert node.creators == [{"name": "Doe, J."}]
    # Client called exactly once — no concept re-fetch needed.
    assert adapter._client.get_record.call_count == 1


def test_fetch_record_version_redirects_to_concept(adapter):
    """When seeded with a version URL whose conceptrecid != id, fetch the concept."""
    version_payload = {
        "id": 7234562,
        "conceptrecid": "7234500",  # parent concept
        "doi": "10.5281/zenodo.7234562",
        "conceptdoi": "10.5281/zenodo.7234500",
        "metadata": {"version": "2.0.0"},
    }
    concept_payload = {
        "id": 7234500,
        "conceptrecid": "7234500",
        "doi": "10.5281/zenodo.7234500",
        "conceptdoi": "10.5281/zenodo.7234500",
        "metadata": {
            "title": "Versioned package",
            "publication_date": "2023-01-15",
            "resource_type": {"id": "software"},
            "license": {"id": "MIT"},
            "access_right": "open",
            "relations": {"version": [{
                "is_last": True,
                "index": 1,
                "parent": {"pid_value": "7234500"},
            }]},
        },
    }
    adapter._client.get_record.side_effect = [version_payload, concept_payload]
    node = adapter.fetch("https://zenodo.org/records/7234562")
    # Returned node IS the concept record:
    assert node.url == "https://zenodo.org/records/7234500"
    assert node.doi == "10.5281/zenodo.7234500"
    assert node.concept_doi == "10.5281/zenodo.7234500"
    # Two API calls: the version, then the concept.
    assert adapter._client.get_record.call_count == 2


def test_fetch_unknown_path_returns_none(adapter):
    assert adapter.fetch("https://zenodo.org/about") is None
