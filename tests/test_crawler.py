"""Tests for the GitHubCrawler engine."""

from unittest.mock import MagicMock

from open_pulse_crawler.crawler import GitHubCrawler


def test_crawl_honors_cancel_requested_set_before_start():
    """`cancel_requested` set before crawl() runs halts before any round executes."""
    client = MagicMock()
    crawler = GitHubCrawler(client=client, max_rounds=3, batch_size=1)
    crawler.cancel_requested = True

    crawler.add_seeds(["someuser"])
    assert len(crawler.queue) == 1  # seed queued

    crawler.crawl(show_progress=False)

    # The round-boundary cancel check fires immediately: no round processed.
    assert crawler.current_round == 0
    assert len(crawler.graph.users) == 0
    assert len(crawler.graph.orgs) == 0
    assert len(crawler.graph.repos) == 0
    # The seed is still queued — nothing was consumed.
    assert len(crawler.queue) == 1


def test_crawl_without_cancel_runs_normally():
    """A crawler with cancel/pause unset behaves as before (no-op guards)."""
    client = MagicMock()
    crawler = GitHubCrawler(client=client, max_rounds=1, batch_size=1)
    assert crawler.cancel_requested is False
    assert crawler.pause_requested is False
    # Empty queue -> crawl loop body never runs, completes cleanly.
    crawler.crawl(show_progress=False)
    assert crawler.current_round == 0
