"""Tests for the --max-contributors skip rule.

The rule lives in ``GitHubCrawler._process_repository`` and trips when a
repo's ``contributor_count`` exceeds the configured threshold. The repo
node still lands in the graph; only its contributors are not queued.
"""

from __future__ import annotations

import threading
from types import SimpleNamespace
from typing import Optional
from unittest.mock import MagicMock

import pytest

from open_pulse_crawler.crawler import GitHubCrawler


def _fake_client(
    cached_repo: Optional[dict] = None,
    contributor_count_fallback: Optional[int] = None,
) -> MagicMock:
    """Build a minimal client that satisfies what _process_repository touches."""
    client = MagicMock()
    # The crawler reads ``client.semaphore._value`` to default batch_size.
    client.semaphore = SimpleNamespace(_value=1)
    # Cache-miss branch isn't used in these tests, but the crawler reads
    # ``client.cache`` even when crawl_dependencies / crawl_dependents are off.
    # Returning ``None`` is the documented "no cache" sentinel.
    client.cache = None
    # Cached repo dict (returned by get_repository on cache hit).
    client.get_repository.return_value = cached_repo
    # Fallback path when ``contributor_count`` is missing from the cached entry.
    client.get_contributor_count.return_value = contributor_count_fallback
    return client


def _users_enqueued(crawler: GitHubCrawler) -> list[str]:
    """Return logins enqueued via items_to_queue → BFS frontier."""
    return [identifier for kind, identifier, _round in crawler.queue if kind == "user"]


# ── Cached path ─────────────────────────────────────────────────────────────


class TestCachedPathSkip:
    def test_skip_when_count_above_threshold(self):
        cached = {
            "full_name": "linus/big",
            "name": "big",
            "id": 1,
            "owner": "linus",
            "owner_type": "User",
            "is_fork": False,
            "parent": None,
            "contributors": ["a", "b", "c"],  # would be queued without the rule
            "contributor_count": 5000,
        }
        client = _fake_client(cached_repo=cached)
        crawler = GitHubCrawler(client=client, max_contributors=200, batch_size=1)

        repo = crawler._process_repository("linus/big")

        assert repo is not None
        assert repo.skipped_high_contributors is True
        assert repo.contributor_count == 5000
        assert repo.contributors == []
        # Owner is still queued; contributors are not.
        assert "a" not in _users_enqueued(crawler)
        assert "b" not in _users_enqueued(crawler)
        # And no extra HTTP fetch was needed — the count came from cache.
        client.get_contributor_count.assert_not_called()

    def test_keep_when_count_below_threshold(self):
        cached = {
            "full_name": "small/repo",
            "name": "repo",
            "id": 2,
            "owner": "small",
            "owner_type": "User",
            "is_fork": False,
            "parent": None,
            "contributors": ["alice", "bob"],
            "contributor_count": 7,
        }
        client = _fake_client(cached_repo=cached)
        crawler = GitHubCrawler(client=client, max_contributors=200, batch_size=1)

        repo = crawler._process_repository("small/repo")

        assert repo is not None
        assert repo.skipped_high_contributors is False
        assert repo.contributor_count == 7
        assert repo.contributors == ["alice", "bob"]
        assert "alice" in _users_enqueued(crawler)
        assert "bob" in _users_enqueued(crawler)

    def test_no_skip_when_threshold_unset(self):
        cached = {
            "full_name": "linus/big",
            "name": "big",
            "id": 3,
            "owner": "linus",
            "owner_type": "User",
            "is_fork": False,
            "parent": None,
            "contributors": ["a", "b"],
            "contributor_count": 5000,
        }
        client = _fake_client(cached_repo=cached)
        # max_contributors=None means: never skip, regardless of count.
        crawler = GitHubCrawler(client=client, max_contributors=None, batch_size=1)

        repo = crawler._process_repository("linus/big")

        assert repo is not None
        assert repo.skipped_high_contributors is False
        assert repo.contributors == ["a", "b"]

    def test_fallback_count_lookup_when_cache_lacks_count(self):
        """Older cache entries don't carry ``contributor_count``; fall back to a
        single live call (which then write-throughs into the cache)."""
        cached = {
            "full_name": "old/entry",
            "name": "entry",
            "id": 4,
            "owner": "old",
            "owner_type": "User",
            "is_fork": False,
            "parent": None,
            "contributors": ["x", "y"],
            # Note: no 'contributor_count' key — this simulates a cache entry
            # written before the field existed.
        }
        client = _fake_client(cached_repo=cached, contributor_count_fallback=900)
        crawler = GitHubCrawler(client=client, max_contributors=100, batch_size=1)

        repo = crawler._process_repository("old/entry")

        assert repo is not None
        client.get_contributor_count.assert_called_once_with("old/entry")
        assert repo.contributor_count == 900
        assert repo.skipped_high_contributors is True
        assert repo.contributors == []
        assert "x" not in _users_enqueued(crawler)


# ── Owner / fork edges still propagate when contributors are skipped ────────


class TestSkipPreservesNonContributorEdges:
    def test_owner_still_enqueued_when_skipped(self):
        cached = {
            "full_name": "mega/proj",
            "name": "proj",
            "id": 5,
            "owner": "mega",
            "owner_type": "Organization",
            "is_fork": False,
            "parent": None,
            "contributors": ["zzz"],
            "contributor_count": 9999,
        }
        client = _fake_client(cached_repo=cached)
        crawler = GitHubCrawler(client=client, max_contributors=10, batch_size=1)

        repo = crawler._process_repository("mega/proj")

        assert repo is not None
        assert repo.skipped_high_contributors is True
        # Owner edge survives — the org is still queued for exploration.
        org_logins = [
            identifier for kind, identifier, _round in crawler.queue if kind == "org"
        ]
        assert "mega" in org_logins
