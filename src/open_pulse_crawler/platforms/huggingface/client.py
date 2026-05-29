"""Thin httpx wrapper for huggingface.co's REST API.

Mirrors the DataCite client's shape. Pagination follows the ``Link`` header
with ``cursor=<opaque>`` query parameter (same shape as GitLab keyset). 429
responses honor the ``Retry-After`` header with one automatic retry, then
raise.
"""
from __future__ import annotations

import logging
import re
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import httpx

logger = logging.getLogger(__name__)

# HuggingFace's default page size is 100 (max for list endpoints).
DEFAULT_PAGE_SIZE = 100

# Cap Retry-After honored — avoid hanging on a rogue server response.
MAX_RETRY_AFTER_SECONDS = 60

# Regex for extracting the next-page URL from a Link header value:
#   <https://huggingface.co/api/models?cursor=...&limit=100>; rel="next"
_LINK_NEXT_RE = re.compile(r'<(?P<url>[^>]+)>;\s*rel="next"')


class HuggingFaceHTTPClient:
    """HTTP client for ``huggingface.co`` API endpoints.

    Authentication: ``Authorization: Bearer hf_...`` when ``tokens`` is
    non-empty. Anonymous (no header) when ``tokens=[]`` — HuggingFace's
    public reads return 200 for all entity endpoints we crawl.

    Rate-limit handling: on HTTP 429, the client reads the ``Retry-After``
    header (or defaults to 5 seconds), sleeps, and retries once. A second
    429 raises ``httpx.HTTPStatusError``.
    """

    def __init__(
        self,
        host: str,
        tokens: List[str],
        _cache_dir: Optional[Path] = None,
    ) -> None:
        self.host = host
        self.base_url = f"https://{host}"
        self.tokens = list(tokens)
        self._idx = 0
        self._session = httpx.Client(
            base_url=self.base_url,
            timeout=httpx.Timeout(20.0, connect=10.0),
            follow_redirects=True,
            headers={"Accept": "application/json"},
        )
        if not self.tokens:
            logger.warning(
                "HuggingFaceHTTPClient(%s) constructed with no tokens — "
                "anonymous reads only; rate limits will be tighter.",
                host,
            )
        else:
            self._apply_current_token()

        self._cache: Optional[Any] = None
        if _cache_dir is not None:
            from ..github.client import APICache, resolve_cache_ttl
            self._cache = APICache(
                _cache_dir,
                ttl_seconds=resolve_cache_ttl(),
                host=self.host,
            )

    # ---- token rotation ---------------------------------------------------

    def _apply_current_token(self) -> None:
        self._session.headers["Authorization"] = f"Bearer {self.tokens[self._idx]}"

    def _rotate(self) -> None:
        """Cycle to the next token. No-op in anonymous mode."""
        if not self.tokens:
            return
        self._idx = (self._idx + 1) % len(self.tokens)
        self._apply_current_token()
