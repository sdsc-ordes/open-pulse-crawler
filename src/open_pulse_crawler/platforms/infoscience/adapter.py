"""Infoscience PlatformAdapter — classify / fetch / expand / normalize_uri.

See ``docs/superpowers/specs/2026-05-28-infoscience-adapter-design.md``.
"""
from __future__ import annotations

import logging
import re
from typing import Any, ClassVar, Dict, Iterable, List, Optional
from urllib.parse import urlparse

from ...models import (
    InfoscienceItem, InfosciencePerson, InfoscienceOrgUnit,
)
from ...node_id import NodeKind, canonical_url
from ..base import Edge, ExpandOpts, PlatformAdapter, RateLimitInfo
from .client import InfoscienceClient

logger = logging.getLogger(__name__)

_HANDLE_PATH = re.compile(r"^handle/(?P<handle>[^/]+/[^/]+)$")
_UUID_PATH = re.compile(r"^server/api/core/items/(?P<uuid>[0-9a-fA-F-]+)$")


class InfoscienceAdapter(PlatformAdapter):
    platform: ClassVar[str] = "infoscience"

    def __init__(self, client: InfoscienceClient, instance_host: str) -> None:
        self._client = client
        self.instance_host = instance_host
        # uuid → "20.500.14299/<id>" mapping, populated lazily.
        self._uuid_to_handle: Dict[str, str] = {}

    # ---- normalize_uri -----------------------------------------------------

    def normalize_uri(self, raw: str) -> str:
        """Return the canonical Infoscience handle URL for ``raw``.

        Accepts canonical ``/handle/<prefix>/<id>`` URLs and ``/server/api/core/items/<uuid>``
        URLs. UUID-form URLs are resolved via a cached fetch; if the UUID
        doesn't resolve, the UUID-form URL is returned unchanged so the
        BFS engine can still queue it (the eventual ``fetch`` call will
        return ``None`` and the node is skipped).
        """
        if not isinstance(raw, str) or not raw:
            raise ValueError(f"empty or non-string seed: {raw!r}")

        parts = urlparse(raw)
        host = (parts.netloc or self.instance_host).lower()
        path = parts.path or "/"

        # UUID-form? Resolve to handle.
        m = _UUID_PATH.match(path.strip("/"))
        if m:
            uuid = m.group("uuid")
            handle = self._uuid_to_handle.get(uuid)
            if handle is None:
                raw_obj = self._client.get_item_by_uuid(uuid)
                if raw_obj is not None:
                    handle = raw_obj.get("handle")
                    if handle:
                        self._uuid_to_handle[uuid] = handle
            if handle:
                return canonical_url(host, f"/handle/{handle}")
            # Couldn't resolve; return the UUID-form URL unchanged.
            return canonical_url(host, path)

        return canonical_url(host, path)

    # ---- classify ----------------------------------------------------------

    def classify(self, uri: str) -> Optional[NodeKind]:
        """All Infoscience entity types share the ``/handle/`` URL form.

        Returns ``USER_OR_ORG`` for any handle URL; ``fetch`` does the real
        dispatch via the server-side ``entityType`` discriminator.
        """
        path = urlparse(self.normalize_uri(uri)).path.strip("/")
        if _HANDLE_PATH.match(path):
            return NodeKind.USER_OR_ORG
        return None

    # ---- fetch (placeholder — Task 6 implements) ---------------------------

    def fetch(self, uri: str):
        raise NotImplementedError("Task 6 implements fetch()")

    # ---- expand (placeholder — Task 7 implements) --------------------------

    def expand(self, node, opts: ExpandOpts) -> Iterable[Edge]:
        raise NotImplementedError("Task 7 implements expand()")

    # ---- rate_limit --------------------------------------------------------

    def rate_limit_state(self) -> RateLimitInfo:
        # DSpace doesn't reliably surface rate-limit headers; return
        # conservative placeholders matching the Zenodo adapter pattern.
        return RateLimitInfo(remaining=1000, limit=2000, reset_at=None)
