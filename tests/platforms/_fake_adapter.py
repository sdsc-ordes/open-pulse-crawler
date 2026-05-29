"""Stub ``PlatformAdapter`` for crawler dispatch tests.

The fake's ``fetch`` and ``expand`` are wholly driven by two dicts that
the test sets up ahead of time:

* ``fetched`` maps a node URI to the model instance ``fetch`` should
  return (``None`` if the URI isn't in the dict, mirroring the production
  contract).
* ``edges`` maps a node URI to the list of :class:`Edge` instances that
  ``expand`` should yield for that node.

This is enough to exercise the crawler's BFS dispatch end-to-end without
touching the network or the real ``GitHubClient``.
"""
from __future__ import annotations

from typing import Any, ClassVar, Iterable

from open_pulse_crawler.platforms.base import (
    Edge,
    ExpandOpts,
    PlatformAdapter,
    RateLimitInfo,
)


class FakePlatformAdapter(PlatformAdapter):
    """Minimal ``PlatformAdapter`` implementation for tests."""

    platform: ClassVar[str] = "fake"

    def __init__(self, instance_host: str = "fake.test") -> None:
        self.instance_host = instance_host
        # Test fixtures assign these directly.
        self.fetched: dict[str, Any] = {}
        self.edges: dict[str, list[Edge]] = {}

    def normalize_uri(self, raw: str) -> str:
        return raw.rstrip("/")

    def classify(self, uri: str):
        # The crawler's dual-path dispatch never calls classify on the
        # adapter path — it goes straight to fetch/expand. Returning
        # ``None`` here keeps the contract honest without forcing tests
        # to set up classification rules.
        return None

    def fetch(self, uri: str):
        return self.fetched.get(uri)

    def expand(self, node, opts: ExpandOpts) -> Iterable[Edge]:
        url = getattr(node, "url", None) or getattr(node, "id", None)
        return iter(self.edges.get(str(url), []))

    def rate_limit_state(self) -> RateLimitInfo:
        return RateLimitInfo(remaining=999, limit=1000)
