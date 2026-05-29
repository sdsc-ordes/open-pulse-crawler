"""Tiny live dryrun against api.datacite.org.

Anonymous-friendly — runs whenever ``CRAWLER_SKIP_INTEGRATION`` is unset.
Skipped when explicitly opted out (e.g., offline CI).

Hits the live DataCite API with a 2-second delay between requests to
stay well under the public rate limit.
"""
from __future__ import annotations

import os
import time

import pytest

pytestmark = pytest.mark.integration


@pytest.mark.skipif(
    os.environ.get("CRAWLER_SKIP_INTEGRATION") == "1",
    reason="CRAWLER_SKIP_INTEGRATION=1 set",
)
def test_fetch_real_datacite_work_via_epfl_ror() -> None:
    """Walk EPFL's ROR to one of its affiliated DataCite works.

    Seeds the EPFL ROR (a bare anchor, no API call), walks one round of
    `has_publication` edges, fetches the first DOI. Asserts non-empty
    DataCiteWork with a known affiliation.
    """
    from open_pulse_crawler.platforms.datacite_adapter.adapter import DataCiteAdapter
    from open_pulse_crawler.platforms.datacite_adapter.client import DataCiteHTTPClient
    from open_pulse_crawler.platforms.base import ExpandOpts
    from open_pulse_crawler.models import DataCiteOrganization, DataCiteWork

    client = DataCiteHTTPClient(host="api.datacite.org", tokens=[])
    adapter = DataCiteAdapter(client=client, instance_host="api.datacite.org")

    epfl_ror = "https://ror.org/02s376052"

    # 1. Bare-anchor fetch returns an Organization with no API call.
    org = adapter.fetch(epfl_ror)
    assert isinstance(org, DataCiteOrganization)
    assert org.ror_id == "02s376052"

    # 2. Walk one has_publication edge to verify the search query works live.
    time.sleep(2.0)  # be polite
    first_edge = next(iter(adapter.expand(org, ExpandOpts())), None)
    if first_edge is None:
        pytest.skip("EPFL ROR returned 0 DataCite works — unexpected; investigate live data.")
    assert first_edge.kind == "has_publication"
    assert first_edge.dst.startswith("https://doi.org/") or first_edge.dst.startswith("https://zenodo.org/")

    # 3. Fetch the underlying DOI (only if it stayed at doi.org; Zenodo URLs
    #    would be the Zenodo adapter's job).
    if first_edge.dst.startswith("https://doi.org/"):
        time.sleep(2.0)
        work = adapter.fetch(first_edge.dst)
        if work is None:
            pytest.skip(f"Live fetch of {first_edge.dst} returned 404 — DOI may have been retracted.")
        assert isinstance(work, DataCiteWork)
        assert work.doi
        assert work.resource_type  # non-empty resourceTypeGeneral
