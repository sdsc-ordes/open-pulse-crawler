"""Tests for ``ZenodoAdapter`` — classify / normalize_uri / fetch / expand.

Tasks 6 and 7 of the Zenodo adapter plan.
"""
from unittest.mock import MagicMock

import pytest

from open_pulse_crawler.models import (
    ZenodoCommunityModel,
    ZenodoRecordModel,
    ZenodoUserModel,
)
from open_pulse_crawler.node_id import NodeKind
from open_pulse_crawler.platforms.base import Edge, ExpandOpts
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


# --- expand ---------------------------------------------------------

def _stub_record(adapter, *, communities=(), owners=(), related=()):
    return ZenodoRecordModel(
        url="https://zenodo.org/records/7234562",
        full_name="10.5281/zenodo.7234562",
        platform="zenodo",
        doi="10.5281/zenodo.7234562",
        concept_doi="10.5281/zenodo.7234562",
        extras={
            "_raw_metadata": {
                "communities": [{"identifier": c} for c in communities],
                "owners": list(owners),
                "related_identifiers": list(related),
            },
        },
    )


def test_expand_record_emits_in_community_edge():
    a = ZenodoAdapter(client=MagicMock(), instance_host="zenodo.org")
    record = _stub_record(a, communities=["sdsc-ordes", "swiss-research"])
    edges = list(a.expand(record, ExpandOpts()))
    in_comm = [e for e in edges if e.kind == "in_community"]
    assert {e.dst for e in in_comm} == {
        "https://zenodo.org/communities/sdsc-ordes",
        "https://zenodo.org/communities/swiss-research",
    }
    assert all(e.src == record.url for e in in_comm)


def test_expand_record_emits_uploaded_by_edge():
    a = ZenodoAdapter(client=MagicMock(), instance_host="zenodo.org")
    record = _stub_record(a, owners=[12345])
    edges = list(a.expand(record, ExpandOpts()))
    by = [e for e in edges if e.kind == "uploaded_by"]
    assert by == [
        Edge(src=record.url, kind="uploaded_by", dst="https://zenodo.org/users/12345")
    ]


def test_expand_record_emits_related_to_with_relation_in_kind():
    a = ZenodoAdapter(client=MagicMock(), instance_host="zenodo.org")
    record = _stub_record(a, related=[
        {"identifier": "https://github.com/sdsc-ordes/gimie",
         "relation": "isSupplementTo", "scheme": "url"},
        {"identifier": "10.5281/zenodo.99",
         "relation": "cites", "scheme": "doi"},
        # arXiv now synthesized to arxiv.org URL (was previously skipped).
        {"identifier": "arXiv:2401.12345", "relation": "cites", "scheme": "arxiv"},
    ])
    edges = [e for e in a.expand(record, ExpandOpts()) if e.kind.startswith("related_to.")]
    kinds_dsts = sorted((e.kind, e.dst) for e in edges)
    assert kinds_dsts == [
        ("related_to.cites", "https://arxiv.org/abs/2401.12345"),
        ("related_to.cites", "https://zenodo.org/records/99"),
        ("related_to.isSupplementTo", "https://github.com/sdsc-ordes/gimie"),
    ]


# --- _synthesize_target_url (scheme → canonical URL) ----------------

def test_synthesize_passthrough_https_under_any_scheme():
    """Identifiers that are already https:// always pass through verbatim,
    regardless of declared scheme — Zenodo sometimes stamps the URL into the
    identifier and declares scheme='doi' or scheme='arxiv'."""
    fn = ZenodoAdapter._synthesize_target_url
    assert fn("doi", "https://doi.org/10.1234/x") == "https://doi.org/10.1234/x"
    assert fn("arxiv", "https://arxiv.org/abs/2401.12345") == "https://arxiv.org/abs/2401.12345"
    assert fn("url", "https://example.com/foo") == "https://example.com/foo"


def test_synthesize_arxiv_strips_prefix():
    fn = ZenodoAdapter._synthesize_target_url
    assert fn("arxiv", "2401.12345") == "https://arxiv.org/abs/2401.12345"
    assert fn("arxiv", "arXiv:2401.12345") == "https://arxiv.org/abs/2401.12345"
    assert fn("arxiv", "ARXIV:2401.12345") == "https://arxiv.org/abs/2401.12345"


def test_synthesize_orcid_handles_prefix_and_bare():
    fn = ZenodoAdapter._synthesize_target_url
    assert fn("orcid", "0000-0002-1825-0097") == "https://orcid.org/0000-0002-1825-0097"
    assert fn("orcid", "ORCID:0000-0002-1825-0097") == "https://orcid.org/0000-0002-1825-0097"


def test_synthesize_pmid():
    fn = ZenodoAdapter._synthesize_target_url
    assert fn("pmid", "12345678") == "https://pubmed.ncbi.nlm.nih.gov/12345678/"


def test_synthesize_pmcid_normalizes_prefix():
    fn = ZenodoAdapter._synthesize_target_url
    assert fn("pmcid", "PMC1234567") == "https://www.ncbi.nlm.nih.gov/pmc/articles/PMC1234567/"
    assert fn("pmcid", "1234567") == "https://www.ncbi.nlm.nih.gov/pmc/articles/PMC1234567/"
    # Lowercase prefix should canonicalize to PMC
    assert fn("pmcid", "pmc1234567") == "https://www.ncbi.nlm.nih.gov/pmc/articles/PMC1234567/"


def test_synthesize_swh_urn():
    fn = ZenodoAdapter._synthesize_target_url
    assert fn("swh", "swh:1:dir:abc123") == "https://archive.softwareheritage.org/swh:1:dir:abc123"
    assert fn("swh", "swh:1:cnt:deadbeef") == "https://archive.softwareheritage.org/swh:1:cnt:deadbeef"


def test_synthesize_doi_zenodo_rewrites_to_platform_url():
    fn = ZenodoAdapter._synthesize_target_url
    # Zenodo prefix (10.5281) → canonical platform URL
    assert fn("doi", "10.5281/zenodo.99") == "https://zenodo.org/records/99"
    # Sandbox prefix (10.5072) → sandbox platform URL
    assert fn("doi", "10.5072/zenodo.42") == "https://sandbox.zenodo.org/records/42"
    # Non-Zenodo DOI falls back to doi.org URL
    assert fn("doi", "10.1234/foo.bar") == "https://doi.org/10.1234/foo.bar"


def test_synthesize_unknown_scheme_drops_non_url_identifier():
    fn = ZenodoAdapter._synthesize_target_url
    # Unknown scheme + non-URL identifier → None
    assert fn("isbn", "978-3-16-148410-0") is None
    assert fn("issn", "0001-1234") is None
    assert fn("ark", "ark:/12345/abc") is None


def test_synthesize_empty_inputs():
    fn = ZenodoAdapter._synthesize_target_url
    assert fn("doi", "") is None
    assert fn("url", "") is None
    assert fn("", "") is None


def test_expand_record_emits_all_synthesized_schemes():
    """End-to-end: a record with five different scheme entries emits five
    edges with the right target URLs and the relation in the kind."""
    a = ZenodoAdapter(client=MagicMock(), instance_host="zenodo.org")
    record = _stub_record(a, related=[
        {"identifier": "2401.12345", "relation": "cites", "scheme": "arxiv"},
        {"identifier": "0000-0002-1825-0097", "relation": "isReferencedBy", "scheme": "orcid"},
        {"identifier": "12345678", "relation": "documents", "scheme": "pmid"},
        {"identifier": "PMC987654", "relation": "isCitedBy", "scheme": "pmcid"},
        {"identifier": "swh:1:dir:abc", "relation": "isSupplementTo", "scheme": "swh"},
    ])
    edges = [e for e in a.expand(record, ExpandOpts()) if e.kind.startswith("related_to.")]
    dsts = {e.kind: e.dst for e in edges}
    assert dsts["related_to.cites"] == "https://arxiv.org/abs/2401.12345"
    assert dsts["related_to.isReferencedBy"] == "https://orcid.org/0000-0002-1825-0097"
    assert dsts["related_to.documents"] == "https://pubmed.ncbi.nlm.nih.gov/12345678/"
    assert dsts["related_to.isCitedBy"] == "https://www.ncbi.nlm.nih.gov/pmc/articles/PMC987654/"
    assert dsts["related_to.isSupplementTo"] == "https://archive.softwareheritage.org/swh:1:dir:abc"


def test_expand_community_emits_contains_edges():
    a = ZenodoAdapter(client=MagicMock(), instance_host="zenodo.org")
    a._client.iter_community_records.return_value = iter([
        {"id": 1}, {"id": 2}, {"id": 3},
    ])
    community = ZenodoCommunityModel(
        url="https://zenodo.org/communities/sdsc-ordes",
        login="sdsc-ordes",
        platform="zenodo",
    )
    edges = list(a.expand(community, ExpandOpts()))
    contains = [e for e in edges if e.kind == "contains"]
    assert sorted(e.dst for e in contains) == [
        "https://zenodo.org/records/1",
        "https://zenodo.org/records/2",
        "https://zenodo.org/records/3",
    ]
    assert all(e.src == community.url for e in contains)


def test_expand_user_emits_uploaded_edges():
    a = ZenodoAdapter(client=MagicMock(), instance_host="zenodo.org")
    a._client.iter_user_records.return_value = iter([{"id": 7}, {"id": 8}])
    user = ZenodoUserModel(
        url="https://zenodo.org/users/12345",
        login="alice",
        platform="zenodo",
    )
    edges = list(a.expand(user, ExpandOpts()))
    uploaded = [e for e in edges if e.kind == "uploaded"]
    assert sorted(e.dst for e in uploaded) == [
        "https://zenodo.org/records/7",
        "https://zenodo.org/records/8",
    ]
    assert all(e.src == user.url for e in uploaded)


def test_expand_user_with_no_accessible_records_yields_nothing():
    """iter_user_records degrades to [] on auth-required endpoints → no edges."""
    a = ZenodoAdapter(client=MagicMock(), instance_host="zenodo.org")
    a._client.iter_user_records.return_value = iter([])
    user = ZenodoUserModel(
        url="https://zenodo.org/users/12345",
        login="alice",
        platform="zenodo",
    )
    edges = list(a.expand(user, ExpandOpts()))
    assert edges == []
