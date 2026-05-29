"""HuggingFace PlatformAdapter — classify / fetch / expand / normalize_uri.

See ``docs/superpowers/specs/2026-05-29-huggingface-adapter-design.md``.
"""
from __future__ import annotations

import logging
import re
from typing import Any, ClassVar, Dict, Iterable, List, Optional
from urllib.parse import urlsplit

from ...models import (
    HuggingFaceUser, HuggingFaceOrg, HuggingFaceRepo,
    HuggingFacePaper, HuggingFaceCollection,
)
from ...node_id import NodeKind, canonical_url
from ..base import Edge, ExpandOpts, PlatformAdapter, RateLimitInfo
from ..datacite import synthesize_target_url
from .client import HuggingFaceHTTPClient

logger = logging.getLogger(__name__)

# Reserved first-segment words that aren't usernames/org names.
# These are HuggingFace platform routes, not owner accounts.
_RESERVED_FIRST_SEGMENTS = {
    "datasets", "spaces", "papers", "collections",
    "blog", "docs", "tasks", "learn", "pricing",
    "enterprise", "inference-endpoints",
}

# Path patterns (match against the URL path with leading slash stripped + trailing slash stripped).
_PAPER_PATH      = re.compile(r"^papers/(?P<arxiv>\d{4}\.\d+(?:v\d+)?)$")
_DATASET_PATH    = re.compile(r"^datasets/(?P<owner>[^/]+)/(?P<name>[^/]+)$")
_SPACE_PATH      = re.compile(r"^spaces/(?P<owner>[^/]+)/(?P<name>[^/]+)$")
_COLLECTION_PATH = re.compile(r"^collections/(?P<owner>[^/]+)/(?P<slug>[^/]+)$")
_MODEL_PATH      = re.compile(r"^(?P<owner>[^/]+)/(?P<name>[^/]+)$")
_USER_OR_ORG_PATH = re.compile(r"^(?P<name>[^/]+)$")


class HuggingFaceAdapter(PlatformAdapter):
    platform: ClassVar[str] = "huggingface"

    def __init__(self, client: HuggingFaceHTTPClient, instance_host: str) -> None:
        self._client = client
        self.instance_host = instance_host

    # ---- normalize_uri -----------------------------------------------------

    def normalize_uri(self, raw: str) -> str:
        """Return the canonical HuggingFace URL for ``raw``.

        Lowercases the host, strips query/fragment, collapses trailing
        slashes. Does NOT validate that the path matches a known entity
        shape — that's classify's job.
        """
        if not isinstance(raw, str) or not raw:
            raise ValueError(f"empty or non-string seed: {raw!r}")

        parts = urlsplit(raw)
        host = (parts.netloc or self.instance_host).lower()
        path = (parts.path or "/").rstrip("/")
        if not path:
            path = "/"
        return canonical_url(host, path)

    # ---- classify ----------------------------------------------------------

    def classify(self, uri: str) -> Optional[NodeKind]:
        """Return the NodeKind for ``uri``, based on URL path shape.

        Path-prefix order matters: reserved prefixes (datasets, spaces,
        papers, collections) are checked before the bare-`<owner>/<name>`
        model pattern. A bare single segment that isn't reserved is
        USER_OR_ORG (disambiguated by fetch).
        """
        normed = self.normalize_uri(uri)
        parts = urlsplit(normed)
        host = parts.netloc.lower()
        if host != self.instance_host.lower():
            return None
        path_inner = (parts.path or "/").lstrip("/").rstrip("/")
        if not path_inner:
            return None

        # Reserved prefixes first.
        m = _PAPER_PATH.match(path_inner)
        if m:
            return NodeKind.REPO
        m = _DATASET_PATH.match(path_inner)
        if m:
            return NodeKind.REPO
        m = _SPACE_PATH.match(path_inner)
        if m:
            return NodeKind.REPO
        m = _COLLECTION_PATH.match(path_inner)
        if m:
            return NodeKind.ORG

        # Bare-`owner/name` model: first segment must not be reserved.
        m = _MODEL_PATH.match(path_inner)
        if m and m.group("owner") not in _RESERVED_FIRST_SEGMENTS:
            return NodeKind.REPO

        # Single non-reserved segment → user or org.
        m = _USER_OR_ORG_PATH.match(path_inner)
        if m and m.group("name") not in _RESERVED_FIRST_SEGMENTS:
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
        # HuggingFace doesn't reliably surface rate-limit headers;
        # conservative placeholders matching the Infoscience/DataCite pattern.
        return RateLimitInfo(remaining=1000, limit=2000, reset_at=None)
