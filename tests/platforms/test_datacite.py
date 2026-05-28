# tests/platforms/test_datacite.py
"""Tests for the shared DataCite URL synthesizer.

Lifted from `ZenodoAdapter._synthesize_target_url` static method when both
the Zenodo and Infoscience adapters needed the same scheme→URL mapping.
"""
from open_pulse_crawler.platforms.datacite import synthesize_target_url


def test_synthesize_passthrough_https_under_any_scheme():
    """Identifiers that are already https:// always pass through verbatim,
    regardless of declared scheme."""
    assert synthesize_target_url("doi", "https://doi.org/10.1234/x") == "https://doi.org/10.1234/x"
    assert synthesize_target_url("arxiv", "https://arxiv.org/abs/2401.12345") == "https://arxiv.org/abs/2401.12345"
    assert synthesize_target_url("url", "https://example.com/foo") == "https://example.com/foo"


def test_synthesize_arxiv_strips_prefix():
    assert synthesize_target_url("arxiv", "2401.12345") == "https://arxiv.org/abs/2401.12345"
    assert synthesize_target_url("arxiv", "arXiv:2401.12345") == "https://arxiv.org/abs/2401.12345"
    assert synthesize_target_url("arxiv", "ARXIV:2401.12345") == "https://arxiv.org/abs/2401.12345"


def test_synthesize_orcid_handles_prefix_and_bare():
    assert synthesize_target_url("orcid", "0000-0002-1825-0097") == "https://orcid.org/0000-0002-1825-0097"
    assert synthesize_target_url("orcid", "ORCID:0000-0002-1825-0097") == "https://orcid.org/0000-0002-1825-0097"


def test_synthesize_pmid():
    assert synthesize_target_url("pmid", "12345678") == "https://pubmed.ncbi.nlm.nih.gov/12345678/"


def test_synthesize_pmcid_normalizes_prefix():
    assert synthesize_target_url("pmcid", "PMC1234567") == "https://www.ncbi.nlm.nih.gov/pmc/articles/PMC1234567/"
    assert synthesize_target_url("pmcid", "1234567") == "https://www.ncbi.nlm.nih.gov/pmc/articles/PMC1234567/"
    assert synthesize_target_url("pmcid", "pmc1234567") == "https://www.ncbi.nlm.nih.gov/pmc/articles/PMC1234567/"


def test_synthesize_swh_urn():
    assert synthesize_target_url("swh", "swh:1:dir:abc123") == "https://archive.softwareheritage.org/swh:1:dir:abc123"
    assert synthesize_target_url("swh", "swh:1:cnt:deadbeef") == "https://archive.softwareheritage.org/swh:1:cnt:deadbeef"


def test_synthesize_doi_zenodo_rewrites_to_platform_url():
    assert synthesize_target_url("doi", "10.5281/zenodo.99") == "https://zenodo.org/records/99"
    assert synthesize_target_url("doi", "10.5072/zenodo.42") == "https://sandbox.zenodo.org/records/42"
    assert synthesize_target_url("doi", "10.1234/foo.bar") == "https://doi.org/10.1234/foo.bar"


def test_synthesize_unknown_scheme_drops_non_url_identifier():
    assert synthesize_target_url("isbn", "978-3-16-148410-0") is None
    assert synthesize_target_url("issn", "0001-1234") is None
    assert synthesize_target_url("ark", "ark:/12345/abc") is None


def test_synthesize_empty_inputs():
    assert synthesize_target_url("doi", "") is None
    assert synthesize_target_url("url", "") is None
    assert synthesize_target_url("", "") is None
