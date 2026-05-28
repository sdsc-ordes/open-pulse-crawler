"""Tests for the Infoscience PlatformAdapter."""
from unittest.mock import MagicMock
import pytest

from open_pulse_crawler.models import (
    InfoscienceItem, InfosciencePerson, InfoscienceOrgUnit,
)
from open_pulse_crawler.node_id import NodeKind
from open_pulse_crawler.platforms.infoscience.adapter import InfoscienceAdapter


@pytest.fixture
def adapter():
    client = MagicMock()
    return InfoscienceAdapter(client=client, instance_host="infoscience.epfl.ch")


# --- classify -------------------------------------------------------

def test_classify_handle_url(adapter):
    assert adapter.classify("https://infoscience.epfl.ch/handle/20.500.14299/182247") == NodeKind.USER_OR_ORG


def test_classify_unknown_path(adapter):
    assert adapter.classify("https://infoscience.epfl.ch/about") is None


# --- normalize_uri --------------------------------------------------

def test_normalize_handle_url_strips_trailing_slash(adapter):
    assert adapter.normalize_uri("https://infoscience.epfl.ch/handle/20.500.14299/182247/") == \
        "https://infoscience.epfl.ch/handle/20.500.14299/182247"


def test_normalize_handle_url_lowercases_host(adapter):
    assert adapter.normalize_uri("https://INFOSCIENCE.EPFL.CH/handle/20.500.14299/182247") == \
        "https://infoscience.epfl.ch/handle/20.500.14299/182247"


def test_normalize_uuid_url_resolves_to_handle_via_cache(adapter):
    """A UUID-form URL fetches once to learn the handle, then returns canonical."""
    adapter._client.get_item_by_uuid.return_value = {
        "uuid": "abc-123", "handle": "20.500.14299/182247",
    }
    result = adapter.normalize_uri("https://infoscience.epfl.ch/server/api/core/items/abc-123")
    assert result == "https://infoscience.epfl.ch/handle/20.500.14299/182247"
    # Subsequent lookup hits cache, no second fetch
    adapter._client.get_item_by_uuid.reset_mock()
    second = adapter.normalize_uri("https://infoscience.epfl.ch/server/api/core/items/abc-123")
    assert second == "https://infoscience.epfl.ch/handle/20.500.14299/182247"
    adapter._client.get_item_by_uuid.assert_not_called()


def test_normalize_uuid_url_404_returns_uuid_form_unchanged(adapter):
    """If the UUID can't be resolved, keep the UUID-form URL."""
    adapter._client.get_item_by_uuid.return_value = None
    result = adapter.normalize_uri("https://infoscience.epfl.ch/server/api/core/items/missing")
    assert result == "https://infoscience.epfl.ch/server/api/core/items/missing"


# --- fetch ----------------------------------------------------------

def test_fetch_item_publication(adapter):
    adapter._client.get_item_by_handle.return_value = {
        "uuid": "80f7da77-dc21-430e-88a2-f07ede2cb194",
        "handle": "20.500.14299/182247",
        "entityType": "Publication",
        "name": "A study of X",
        "metadata": {
            "dc.title": [{"value": "A study of X"}],
            "dc.type": [{"value": "journal article"}],
            "dc.date.issued": [{"value": "2024-06-30"}],
            "dc.contributor.author": [
                {"value": "Doe, J.", "authority": "auth-uuid-1", "confidence": 600},
            ],
            "dc.subject": [{"value": "kw1"}, {"value": "kw2"}],
            "dc.identifier.doi": [{"value": "10.1234/foo"}],
            "datacite.rights": [{"value": "CC-BY-4.0"}],
        },
    }
    node = adapter.fetch("https://infoscience.epfl.ch/handle/20.500.14299/182247")
    assert isinstance(node, InfoscienceItem)
    assert node.handle == "20.500.14299/182247"
    assert node.uuid == "80f7da77-dc21-430e-88a2-f07ede2cb194"
    assert node.title == "A study of X"
    assert node.resource_type == "journal article"
    assert node.publication_date == "2024-06-30"
    assert node.doi == "10.1234/foo"
    assert node.license == "CC-BY-4.0"
    assert node.keywords == ["kw1", "kw2"]
    assert len(node.authors) == 1
    assert node.authors[0]["name"] == "Doe, J."
    assert node.authors[0]["authority_uuid"] == "auth-uuid-1"


def test_fetch_person(adapter):
    adapter._client.get_item_by_handle.return_value = {
        "uuid": "31b1115e-c04a-445d-b905-18616ef2aacb",
        "handle": "20.500.14299/99923",
        "entityType": "Person",
        "name": "Molyneaux, Nicholas",
        "metadata": {
            "person.givenname": [{"value": "Nicholas"}],
            "person.familyname": [{"value": "Molyneaux"}],
            "person.identifier.orcid": [{"value": "0000-0001-2345-6789"}],
            "epfl.sciperId": [{"value": "123456"}],
            "person.identifier.scopus-author-id": [{"value": "55512345600"}],
            "person.affiliation.name": [{"value": "TRANSP-OR"}],
            "cris.virtual.parent-organization": [
                {"value": "TRANSP-OR", "authority": "ou-uuid-1"},
            ],
        },
    }
    node = adapter.fetch("https://infoscience.epfl.ch/handle/20.500.14299/99923")
    assert isinstance(node, InfosciencePerson)
    assert node.given_name == "Nicholas"
    assert node.family_name == "Molyneaux"
    assert node.orcid == "0000-0001-2345-6789"
    assert node.sciper_id == "123456"
    assert node.scopus_id == "55512345600"
    assert node.affiliation_name == "TRANSP-OR"
    assert node.affiliation_uuid == "ou-uuid-1"
    assert node.login == "123456"  # SciPer wins as login


def test_fetch_orgunit_with_parent(adapter):
    adapter._client.get_item_by_handle.return_value = {
        "uuid": "aaaa1111-2222-3333-4444-555566667777",
        "handle": "20.500.14299/77777",
        "entityType": "OrgUnit",
        "name": "TRANSP-OR",
        "metadata": {
            "organization.legalName": [{"value": "TRANSPort and mobility Laboratory"}],
            "organization.identifier": [{"value": "TRANSP-OR"}],
            "organization.type": [{"value": "laboratory"}],
            "organization.parentOrganization": [
                {"value": "STI", "authority": "parent-ou-uuid"},
            ],
        },
    }
    node = adapter.fetch("https://infoscience.epfl.ch/handle/20.500.14299/77777")
    assert isinstance(node, InfoscienceOrgUnit)
    assert node.unit_id == "TRANSP-OR"
    assert node.unit_type == "laboratory"
    assert node.parent_uuid == "parent-ou-uuid"
    assert node.login == "TRANSP-OR"  # unit_id wins as login


def test_fetch_orgunit_root_no_parent(adapter):
    adapter._client.get_item_by_handle.return_value = {
        "uuid": "root-uuid",
        "handle": "20.500.14299/1",
        "entityType": "OrgUnit",
        "name": "EPFL",
        "metadata": {"organization.identifier": [{"value": "EPFL"}]},
    }
    node = adapter.fetch("https://infoscience.epfl.ch/handle/20.500.14299/1")
    assert isinstance(node, InfoscienceOrgUnit)
    assert node.parent_uuid is None
    assert node.parent_url is None


def test_fetch_404_returns_none(adapter):
    adapter._client.get_item_by_handle.return_value = None
    assert adapter.fetch("https://infoscience.epfl.ch/handle/20.500.14299/missing") is None


def test_fetch_unknown_entitytype_defaults_to_item(adapter):
    """An item with empty / unknown entityType is treated as a publication."""
    adapter._client.get_item_by_handle.return_value = {
        "uuid": "x", "handle": "20.500.14299/1",
        "entityType": "",
        "metadata": {"dc.title": [{"value": "Untyped"}]},
    }
    node = adapter.fetch("https://infoscience.epfl.ch/handle/20.500.14299/1")
    assert isinstance(node, InfoscienceItem)
    assert node.title == "Untyped"
