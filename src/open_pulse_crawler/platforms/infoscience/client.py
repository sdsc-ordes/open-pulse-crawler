"""Thin httpx wrapper for Infoscience's DSpace 7 REST API.

Mirrors the Zenodo client's shape but speaks HAL+JSON instead of plain
JSON. Pagination follows ``_links.next``. 429 responses honor the
``Retry-After`` header with one automatic retry, then raise.
"""
from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import httpx

logger = logging.getLogger(__name__)

# Anonymous DSpace page-size cap is 100 (no 25 limit like Zenodo).
DEFAULT_PAGE_SIZE = 100

# Cap the Retry-After value the client honors, to avoid hanging on a
# rogue server response. 60 seconds is enough for any reasonable backoff.
MAX_RETRY_AFTER_SECONDS = 60


class InfoscienceClient:
    """HTTP client for Infoscience's DSpace 7 REST endpoints.

    Authentication: ``Authorization: Bearer <token>`` when ``tokens`` is
    non-empty. Anonymous (no header) when ``tokens=[]`` — Infoscience's
    public items, persons, and orgunits are readable without auth.

    Rate-limit handling: on HTTP 429, the client reads the ``Retry-After``
    header (or defaults to 5 seconds), sleeps, and retries the request
    once. A second 429 raises ``httpx.HTTPStatusError`` so callers can
    decide whether to keep trying.
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
            timeout=httpx.Timeout(15.0, connect=8.0),
            follow_redirects=True,
            headers={"Accept": "application/json"},
        )
        if not self.tokens:
            logger.warning(
                "InfoscienceClient(%s) constructed with no tokens — "
                "anonymous reads only; rate limits will be tight.",
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

    # ---- token rotation -----------------------------------------------------

    def _apply_current_token(self) -> None:
        self._session.headers["Authorization"] = f"Bearer {self.tokens[self._idx]}"

    def _rotate(self) -> None:
        """Cycle to the next token. No-op in anonymous mode."""
        if not self.tokens:
            return
        self._idx = (self._idx + 1) % len(self.tokens)
        self._apply_current_token()
