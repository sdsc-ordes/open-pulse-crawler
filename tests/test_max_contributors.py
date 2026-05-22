"""Tests for the per-repo contributor cap (``max_contributors``).

``max_contributors`` is a take-up-to-N limit: a repo with more contributors
than the cap is truncated to its top N — it still contributes, it is never
skipped to zero. Unset, the crawler falls back to a built-in default cap.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import List, Optional
from unittest.mock import MagicMock

from open_pulse_crawler.crawler import GitHubCrawler


def _fake_client(cached_repo: Optional[dict] = None) -> MagicMock:
    """Minimal client satisfying what ``_process_repository`` touches."""
    client = MagicMock()
    client.semaphore = SimpleNamespace(_value=1)  # crawler reads this for batch_size
    client.cache = None  # documented "no cache" sentinel
    client.get_repository.return_value = cached_repo
    return client


def _cached_repo(
    contributors: List[str], contributor_count: int, full_name: str = "o/r"
) -> dict:
    owner = full_name.split("/")[0]
    return {
        "full_name": full_name,
        "name": full_name.split("/")[-1],
        "id": 1,
        "owner": owner,
        "owner_type": "User",
        "is_fork": False,
        "parent": None,
        "contributors": contributors,
        "contributor_count": contributor_count,
    }


def _users_enqueued(crawler: GitHubCrawler) -> List[str]:
    return [ident for kind, ident, _round in crawler.queue if kind == "user"]


def test_takes_up_to_max_contributors():
    """A repo with more contributors than the cap is truncated to the top N."""
    cached = _cached_repo(["a", "b", "c", "d", "e"], contributor_count=135)
    crawler = GitHubCrawler(client=_fake_client(cached), max_contributors=3, batch_size=1)

    repo = crawler._process_repository("o/r")

    assert repo is not None
    assert repo.contributors == ["a", "b", "c"]  # top 3 — NOT 0
    enq = _users_enqueued(crawler)
    assert {"a", "b", "c"}.issubset(enq)
    assert "d" not in enq and "e" not in enq
    assert repo.contributor_count == 135  # full count still recorded as metadata


def test_high_contributor_repo_is_never_skipped():
    """The fix: a big repo yields its contributors, never 0."""
    cached = _cached_repo(["x", "y"], contributor_count=5000)
    crawler = GitHubCrawler(client=_fake_client(cached), max_contributors=80, batch_size=1)

    repo = crawler._process_repository("o/r")

    assert repo is not None
    assert repo.contributors == ["x", "y"]  # everything available (< cap), never 0
    assert {"x", "y"}.issubset(_users_enqueued(crawler))


def test_fewer_contributors_than_cap_takes_all():
    cached = _cached_repo(["alice", "bob"], contributor_count=2)
    crawler = GitHubCrawler(client=_fake_client(cached), max_contributors=200, batch_size=1)

    repo = crawler._process_repository("o/r")

    assert repo.contributors == ["alice", "bob"]


def test_no_cap_when_unset():
    """Unset ``max_contributors`` means no cap — every contributor is taken."""
    contributors = [f"c{i}" for i in range(20)]
    cached = _cached_repo(contributors, contributor_count=20)
    crawler = GitHubCrawler(client=_fake_client(cached), max_contributors=None, batch_size=1)

    repo = crawler._process_repository("o/r")

    assert repo.contributors == contributors  # all 20, no truncation
    assert set(contributors).issubset(_users_enqueued(crawler))


def test_contributor_limit_helper():
    set_cap = GitHubCrawler(client=_fake_client(), max_contributors=50, batch_size=1)
    assert set_cap._contributor_limit() == 50

    unset = GitHubCrawler(client=_fake_client(), max_contributors=None, batch_size=1)
    assert unset._contributor_limit() is None  # None = no cap


def test_owner_edge_survives_truncation():
    """Owner is still queued even when contributors are truncated."""
    cached = _cached_repo(["zzz"], contributor_count=9999, full_name="mega/proj")
    cached["owner_type"] = "Organization"
    crawler = GitHubCrawler(client=_fake_client(cached), max_contributors=5, batch_size=1)

    repo = crawler._process_repository("mega/proj")

    assert repo is not None
    org_logins = [ident for kind, ident, _round in crawler.queue if kind == "org"]
    assert "mega" in org_logins
