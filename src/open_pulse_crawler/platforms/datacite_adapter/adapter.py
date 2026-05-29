"""DataCite Commons PlatformAdapter — classify / fetch / expand / normalize_uri.

See ``docs/superpowers/specs/2026-05-29-datacite-adapter-design.md``.
"""
from __future__ import annotations

import logging
import re
from typing import Any, ClassVar, Dict, Iterable, List, Optional
from urllib.parse import urlsplit

from ...models import (
    DataCiteWork, DataCiteOrganization, DataCitePerson, DataCiteClient,
)
from ...node_id import NodeKind, canonical_url
from ..base import Edge, ExpandOpts, PlatformAdapter, RateLimitInfo
from ..datacite import rewrite_doi_url, synthesize_target_url
from .client import DataCiteHTTPClient

logger = logging.getLogger(__name__)

_DOI_PATH = re.compile(r"^(?P<doi>10\.[^/]+/.+)$")
_ROR_PATH = re.compile(r"^(?P<id>[a-z0-9]+)/?$")
_ORCID_PATH = re.compile(r"^(?P<id>\d{4}-\d{4}-\d{4}-\d{3}[\dX])/?$")
_API_DOI_PATH = re.compile(r"^dois/(?P<doi>10\.[^/]+/.+)$")
_API_CLIENT_PATH = re.compile(r"^clients/(?P<id>[a-z0-9.\-_]+)$")
_COMMONS_REPO_PATH = re.compile(r"^repositories/(?P<id>[a-z0-9.\-_]+)$")
_COMMONS_DOI_ALIAS = re.compile(r"^doi\.org/(?P<doi>10\.[^/]+/.+)$")
_COMMONS_ROR_ALIAS = re.compile(r"^ror\.org/(?P<id>[a-z0-9]+)$")
_COMMONS_ORCID_ALIAS = re.compile(r"^orcid\.org/(?P<id>\d{4}-\d{4}-\d{4}-\d{3}[\dX])$")


class DataCiteAdapter(PlatformAdapter):
    platform: ClassVar[str] = "datacite"

    def __init__(self, client: DataCiteHTTPClient, instance_host: str) -> None:
        self._client = client
        self.instance_host = instance_host

    # ---- normalize_uri -----------------------------------------------------

    def normalize_uri(self, raw: str) -> str:
        """Return the canonical DataCite-adapter URL for ``raw``.

        Accepts doi.org / ror.org / orcid.org / api.datacite.org / commons.datacite.org
        URL forms. For doi.org URLs whose prefix is owned by a sibling
        adapter (Zenodo today), rewrites to that adapter's canonical URL so
        the BFS dispatch routes there.
        """
        if not isinstance(raw, str) or not raw:
            raise ValueError(f"empty or non-string seed: {raw!r}")

        parts = urlsplit(raw)
        host = (parts.netloc or self.instance_host).lower()
        # Strip query + fragment; collapse trailing slash on path.
        path = (parts.path or "/").rstrip("/")
        path_inner = path.lstrip("/")

        # --- doi.org ---
        if host == "doi.org":
            m = _DOI_PATH.match(path_inner)
            if m:
                doi = m.group("doi")
                rewritten = rewrite_doi_url(f"https://doi.org/{doi}")
                if rewritten:
                    return rewritten
                return f"https://doi.org/{doi}"

        # --- ror.org ---
        if host == "ror.org":
            m = _ROR_PATH.match(path_inner)
            if m:
                return f"https://ror.org/{m.group('id')}"

        # --- orcid.org ---
        if host == "orcid.org":
            m = _ORCID_PATH.match(path_inner)
            if m:
                return f"https://orcid.org/{m.group('id')}"

        # --- api.datacite.org ---
        if host == "api.datacite.org":
            m = _API_DOI_PATH.match(path_inner)
            if m:
                doi = m.group("doi")
                rewritten = rewrite_doi_url(f"https://doi.org/{doi}")
                return rewritten or f"https://doi.org/{doi}"
            m = _API_CLIENT_PATH.match(path_inner)
            if m:
                return f"https://commons.datacite.org/repositories/{m.group('id')}"

        # --- commons.datacite.org ---
        if host == "commons.datacite.org":
            m = _COMMONS_REPO_PATH.match(path_inner)
            if m:
                return f"https://commons.datacite.org/repositories/{m.group('id')}"
            m = _COMMONS_DOI_ALIAS.match(path_inner)
            if m:
                doi = m.group("doi")
                rewritten = rewrite_doi_url(f"https://doi.org/{doi}")
                return rewritten or f"https://doi.org/{doi}"
            m = _COMMONS_ROR_ALIAS.match(path_inner)
            if m:
                return f"https://ror.org/{m.group('id')}"
            m = _COMMONS_ORCID_ALIAS.match(path_inner)
            if m:
                return f"https://orcid.org/{m.group('id')}"

        # Fallback: hand back the canonicalized form for the BFS to drop.
        return canonical_url(host, path or "/")

    # ---- classify ----------------------------------------------------------

    def classify(self, uri: str) -> Optional[NodeKind]:
        """Return the NodeKind for ``uri``, based on URL host/path shape."""
        normed = self.normalize_uri(uri)
        parts = urlsplit(normed)
        host = parts.netloc.lower()
        path_inner = (parts.path or "/").lstrip("/").rstrip("/")
        if host == "doi.org" and _DOI_PATH.match(path_inner):
            return NodeKind.REPO
        if host == "ror.org" and _ROR_PATH.match(path_inner):
            return NodeKind.ORG
        if host == "orcid.org" and _ORCID_PATH.match(path_inner):
            return NodeKind.USER
        if host == "commons.datacite.org" and _COMMONS_REPO_PATH.match(path_inner):
            return NodeKind.ORG
        return None

    # ---- fetch (placeholder — Task 6 implements) ---------------------------

    def fetch(self, uri: str):
        raise NotImplementedError("Task 6 implements fetch()")

    # ---- expand (placeholder — Task 7 implements) --------------------------

    def expand(self, node, opts: ExpandOpts) -> Iterable[Edge]:
        raise NotImplementedError("Task 7 implements expand()")

    # ---- rate_limit --------------------------------------------------------

    def rate_limit_state(self) -> RateLimitInfo:
        # DataCite doesn't surface rate-limit headers reliably; conservative
        # placeholders matching the Infoscience adapter pattern.
        return RateLimitInfo(remaining=1000, limit=2000, reset_at=None)
