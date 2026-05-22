"""Tests for the GitHubCrawler engine."""

import threading
from unittest.mock import MagicMock

from open_pulse_crawler.crawler import GitHubCrawler


def test_crawl_honors_stop_event_set_before_start():
    """A stop event set before crawl() runs halts before any round executes."""
    client = MagicMock()
    crawler = GitHubCrawler(client=client, max_rounds=3, batch_size=1)
    crawler.stop_event = threading.Event()
    crawler.stop_event.set()

    crawler.add_seeds(["someuser"])
    assert len(crawler.queue) == 1  # seed queued

    crawler.crawl(show_progress=False)

    # The round-boundary check fires immediately: no round processed.
    assert crawler.current_round == 0
    assert len(crawler.graph.users) == 0
    assert len(crawler.graph.orgs) == 0
    assert len(crawler.graph.repos) == 0
    # The seed is still queued — nothing was consumed.
    assert len(crawler.queue) == 1


def test_crawl_without_stop_event_runs_normally():
    """A crawler with no stop_event behaves exactly as before (no-op guard)."""
    client = MagicMock()
    crawler = GitHubCrawler(client=client, max_rounds=1, batch_size=1)
    assert crawler.stop_event is None
    # Empty queue -> crawl loop body never runs, completes cleanly.
    crawler.crawl(show_progress=False)
    assert crawler.current_round == 0
