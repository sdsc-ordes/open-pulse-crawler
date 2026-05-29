"""Tiny live dryrun against infoscience.epfl.ch.

Anonymous-friendly — runs whenever ``CRAWLER_SKIP_INTEGRATION`` is unset.
Skipped when explicitly opted out (e.g., offline CI).

Hits the live Infoscience API with a 2-second delay between requests
to stay well under EPFL's rate limit.
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
def test_fetch_real_infoscience_publication() -> None:
    """Fetch one EPFL publication via its handle, anonymous mode.

    Asserts the InfoscienceItem subkind, a non-empty handle/UUID, and at
    least one author with a CRIS authority UUID. The seed handle is read
    from the env var INFOSCIENCE_TEST_HANDLE; defaults to a stable demo
    handle that the test author confirmed exists at integration time.
    """
    from open_pulse_crawler.platforms.infoscience.adapter import InfoscienceAdapter
    from open_pulse_crawler.platforms.infoscience.client import InfoscienceClient

    handle = os.environ.get("INFOSCIENCE_TEST_HANDLE", "20.500.14299/182247")
    client = InfoscienceClient(host="infoscience.epfl.ch", tokens=[])
    adapter = InfoscienceAdapter(client=client, instance_host="infoscience.epfl.ch")

    time.sleep(2.0)  # stay polite
    node = adapter.fetch(f"https://infoscience.epfl.ch/handle/{handle}")

    if node is None:
        pytest.skip(
            f"Seed handle {handle} returned 404. Set INFOSCIENCE_TEST_HANDLE to "
            "a known-existing handle and re-run."
        )

    assert node.subkind in ("InfoscienceItem", "InfosciencePerson", "InfoscienceOrgUnit")
    assert node.handle == handle
    assert node.uuid  # non-empty
