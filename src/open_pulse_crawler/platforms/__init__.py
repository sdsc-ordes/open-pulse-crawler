"""PlatformAdapter registry.

The crawler resolves the adapter for a given URI by its host. Hosts are
compared case-insensitively to mirror `node_id.canonical_url` which
lowercases the netloc when canonicalizing.
"""
from __future__ import annotations
from urllib.parse import urlparse

from .base import PlatformAdapter, Edge, ExpandOpts, RateLimitInfo


class PlatformRegistry:
    """Maps `instance_host` → `PlatformAdapter`. Looked up by URI host."""

    def __init__(self) -> None:
        self._adapters: dict[str, PlatformAdapter] = {}

    def register(self, adapter: PlatformAdapter) -> None:
        """Add an adapter. Host is stored lowercased.

        Raises ValueError if an adapter is already registered for the host;
        duplicate registration is almost always a config bug, not intent.
        """
        host = adapter.instance_host.lower()
        if host in self._adapters:
            raise ValueError(f"adapter already registered for host {host!r}")
        self._adapters[host] = adapter

    def adapter_for(self, uri: str) -> PlatformAdapter:
        """Return the adapter responsible for `uri`'s host."""
        host = urlparse(uri).netloc.lower()
        if host not in self._adapters:
            raise KeyError(f"no adapter registered for host {host!r}")
        return self._adapters[host]

    def hosts(self) -> list[str]:
        """Sorted list of registered instance hosts (lowercased)."""
        return sorted(self._adapters)


__all__ = [
    "PlatformAdapter", "PlatformRegistry",
    "Edge", "ExpandOpts", "RateLimitInfo",
]
