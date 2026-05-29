"""BFS dispatch tests for the dual-path (github.com vs adapter) crawler.

Task 6 of the multi-platform refactor introduced a host-based fork in the
BFS dispatch loop: ``github.com`` URIs continue to run through the
existing ``_process_user`` / ``_process_organization`` /
``_process_repository`` helpers, while any other host is handled by the
``PlatformAdapter`` ``fetch + expand`` contract via
:meth:`GitHubCrawler._process_one_via_adapter`.

These tests drive the adapter path with the :class:`FakePlatformAdapter`
stub so they don't need network access or a real ``GitHubClient``.
"""
from unittest.mock import MagicMock

from open_pulse_crawler.crawler import GitHubCrawler
from open_pulse_crawler.models import RepoModel, UserModel
from open_pulse_crawler.platforms import PlatformRegistry
from open_pulse_crawler.platforms.base import Edge

from tests.platforms._fake_adapter import FakePlatformAdapter


def test_dispatch_uses_registered_adapter():
    """Non-github URIs are dispatched through the registry adapter.

    The fake returns a user node, whose ``authored`` edge points at a
    repo node the fake also knows about. After one round the user is in
    the graph; after a second round the repo follows via the enqueued
    counter-party URI.
    """
    fake = FakePlatformAdapter(instance_host="fake.test")
    seed = UserModel(url="https://fake.test/a", login="a", platform="fake")
    repo = RepoModel(url="https://fake.test/a/r", full_name="a/r", platform="fake")
    fake.fetched = {seed.url: seed, repo.url: repo}
    fake.edges = {
        seed.url: [Edge(src=seed.url, kind="authored", dst=repo.url)],
        repo.url: [],
    }
    reg = PlatformRegistry()
    reg.register(fake)

    c = GitHubCrawler(registry=reg, max_rounds=2)
    c.add_seeds([seed.url])
    c.crawl(show_progress=False)

    assert seed.url in c.graph.users
    assert repo.url in c.graph.repos


def test_dispatch_queues_only_unseen_neighbors():
    """Counter-party URIs already visited or queued are not re-enqueued.

    ``a`` follows ``b``; ``b`` follows ``a``. After ``a`` is processed,
    ``b`` is queued. After ``b`` is processed its edge back to ``a``
    must be skipped because ``a`` is already visited — otherwise the BFS
    would loop forever (or, at minimum, blow past ``max_rounds``).
    """
    fake = FakePlatformAdapter()
    a = UserModel(url="https://fake.test/a", login="a", platform="fake")
    b = UserModel(url="https://fake.test/b", login="b", platform="fake")
    fake.fetched = {a.url: a, b.url: b}
    fake.edges = {
        a.url: [Edge(src=a.url, kind="follower_of", dst=b.url)],
        b.url: [Edge(src=b.url, kind="follower_of", dst=a.url)],
    }
    reg = PlatformRegistry()
    reg.register(fake)

    c = GitHubCrawler(registry=reg, max_rounds=5)
    c.add_seeds([a.url])
    c.crawl(show_progress=False)

    assert a.url in c.graph.users
    assert b.url in c.graph.users
    # The crawl converged: rounds advanced only as long as the queue had
    # work for the current round, then stopped. ``current_round`` is
    # bumped once per round actually executed.
    assert c.current_round <= 5
    # And the queue is empty (no straggler back-edge leftovers).
    assert len(c.queue) == 0


def test_dispatch_github_host_uses_legacy_path():
    """``github.com`` URIs bypass the registry — legacy ``_process_*`` runs.

    The registry holds an adapter pinned to ``github.com`` whose
    ``fetch`` raises if it's ever invoked. The legacy path must handle
    the URI without consulting the adapter — proven by the absence of
    that exception.
    """
    raising_fake = FakePlatformAdapter(instance_host="github.com")

    def _raise(*a, **kw):
        raise AssertionError(
            "github.com should not go through adapter in dual-path mode"
        )

    raising_fake.fetch = _raise  # type: ignore[assignment]

    reg = PlatformRegistry()
    reg.register(raising_fake)

    # Build a Crawler with a mocked GitHubClient so the legacy path still
    # has somewhere to call. ``get_user`` returning ``None`` short-circuits
    # the legacy path cleanly — no graph mutation, no extra API calls.
    client = MagicMock()
    client.semaphore._value = 1
    client.get_user.return_value = None
    client.get_organization.return_value = None

    c = GitHubCrawler(client=client, registry=reg, max_rounds=1)
    c.add_seeds(["https://github.com/someone"])
    # Must not raise — the legacy path handled the github.com URI.
    c.crawl(show_progress=False)
    # The legacy path was actually taken — at least one of the legacy
    # client methods must have been called for the seed.
    assert client.get_user.called or client.get_organization.called


def test_pure_registry_mode_get_statistics_returns_none_api_stats():
    from tests.platforms._fake_adapter import FakePlatformAdapter
    from open_pulse_crawler.crawler import GitHubCrawler
    from open_pulse_crawler.platforms import PlatformRegistry

    fake = FakePlatformAdapter()
    reg = PlatformRegistry()
    reg.register(fake)
    c = GitHubCrawler(registry=reg, max_rounds=1)
    stats = c.get_statistics()
    assert stats["api_stats"] is None


def test_add_seeds_accepts_multi_segment_adapter_url():
    """Regression (v2 Infoscience bug): a seed URL whose host is owned by a
    registered non-github adapter and whose path has 3+ segments must NOT
    crash ``add_seeds``.

    ``node_id.kind_of`` only understands 1-segment (user_or_org) and
    2-segment (repo) GitHub paths and raises ``ValueError`` on anything
    deeper — e.g. Infoscience's ``/handle/<prefix>/<id>`` (3 segments).
    Before the fix this propagated up through ``add_seeds`` →
    ``_seed_or_resume`` → ``_run_crawl_v2`` and failed the whole v2 crawl
    during seeding ("kind_of: cannot infer kind for URL ...").

    The seed-enqueue path now consults the registry: for adapter-routed
    hosts it normalizes via the adapter and queues ``user_or_org`` (the
    adapter dispatch re-routes by host and ignores the queued kind).
    """
    reg = PlatformRegistry()
    reg.register(FakePlatformAdapter(instance_host="infoscience.epfl.ch"))
    crawler = GitHubCrawler(registry=reg, max_rounds=1)

    seed = "https://infoscience.epfl.ch/handle/20.500.14299/182247"
    crawler.add_seeds([seed])  # must not raise ValueError

    queued = {(t, u) for (t, u, _r) in crawler.queue}
    assert ("user_or_org", seed) in queued


def test_add_seeds_github_url_still_uses_github_classification():
    """github.com seeds must keep their existing kind_of-based classification
    (2-segment path → repo) — the registry shortcut only applies to
    non-github hosts so legacy behaviour is untouched."""
    client = MagicMock()
    crawler = GitHubCrawler(client=client, max_rounds=1)
    crawler.add_seeds(["https://github.com/torvalds/linux"])
    queued = {(t, u) for (t, u, _r) in crawler.queue}
    assert ("repo", "https://github.com/torvalds/linux") in queued
