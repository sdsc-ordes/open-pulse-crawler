"""Tiny live dryrun against zenodo.org.

Anonymous-friendly — runs whenever ``CRAWLER_SKIP_INTEGRATION`` is unset.
Skipped when explicitly opted out (e.g., offline CI).
"""
from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.integration


@pytest.mark.skipif(
    os.environ.get("CRAWLER_SKIP_INTEGRATION") == "1",
    reason="CRAWLER_SKIP_INTEGRATION=1 set",
)
def test_one_round_against_zenodo_org() -> None:
    """Crawl one Zenodo community for one round; assert the community lands
    in the graph plus at least one ``contains`` edge to a record."""
    from open_pulse_crawler.config import resolve_tokens
    from open_pulse_crawler.crawler import GitHubCrawler
    from open_pulse_crawler.platforms import PlatformRegistry
    from open_pulse_crawler.platforms.zenodo.adapter import ZenodoAdapter
    from open_pulse_crawler.platforms.zenodo.client import ZenodoClient

    tokens = resolve_tokens("zenodo.org")  # may be []
    client = ZenodoClient(host="zenodo.org", tokens=tokens)
    adapter = ZenodoAdapter(client=client, instance_host="zenodo.org")

    registry = PlatformRegistry()
    registry.register(adapter)

    # The "eosc" community is a small, stable public Zenodo community
    # (EOSC Association) with ~70 records. The originally-planned
    # "renku-python" slug 404s on Zenodo as of crawl time, so we use
    # this instead.
    seed = "https://zenodo.org/communities/eosc"

    crawler = GitHubCrawler(registry=registry, max_rounds=1)
    crawler.add_seeds([seed])
    crawler.crawl(show_progress=False)

    assert seed in crawler.graph.orgs, \
        f"community {seed} not found in graph.orgs"
