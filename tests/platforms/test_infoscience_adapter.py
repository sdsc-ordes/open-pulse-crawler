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
