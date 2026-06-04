from open_pulse_crawler.cli import _build_registry
from open_pulse_crawler.platforms.openalex_adapter.adapter import OpenAlexAdapter
from open_pulse_crawler.platforms.datacite_adapter.adapter import DataCiteAdapter


def test_openalex_owns_shared_hosts_when_both_enabled():
    reg, _, _ = _build_registry(["datacite.org", "openalex.org"])
    for h in ["doi.org", "orcid.org", "ror.org", "openalex.org", "api.openalex.org"]:
        assert isinstance(reg.adapter_for(f"https://{h}/x"), OpenAlexAdapter)
    assert isinstance(reg.adapter_for("https://api.datacite.org/x"), DataCiteAdapter)
    assert isinstance(reg.adapter_for("https://commons.datacite.org/x"), DataCiteAdapter)


def test_openalex_fallback_is_datacite_when_both_enabled():
    reg, _, _ = _build_registry(["datacite.org", "openalex.org"])
    oa = reg.adapter_for("https://doi.org/10.1/x")
    assert isinstance(oa.fallback_adapter, DataCiteAdapter)


def test_order_independent():
    reg, _, _ = _build_registry(["openalex.org", "datacite.org"])  # reversed
    assert isinstance(reg.adapter_for("https://doi.org/10.1/x").fallback_adapter, DataCiteAdapter)


def test_openalex_alone_no_fallback():
    reg, _, _ = _build_registry(["openalex.org"])
    oa = reg.adapter_for("https://doi.org/10.1/x")
    assert isinstance(oa, OpenAlexAdapter) and oa.fallback_adapter is None


def test_datacite_alone_keeps_shared_hosts():
    reg, _, _ = _build_registry(["datacite.org"])
    assert isinstance(reg.adapter_for("https://doi.org/10.1/x"), DataCiteAdapter)
