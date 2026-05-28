"""Thin httpx wrapper for Zenodo's InvenioRDM REST API.

Mirrors the GitLab client's shape:
  1. Construction with optional token rotation pool (anonymous when empty).
  2. Single-entity fetches map 404 -> None.  (Task 4)
  3. Iterators degrade to ``[]`` on 401/403 so a missing scope doesn't
     tank the whole crawl.  (Task 5)
  4. Per-host disk cache is pre-allocated when ``_cache_dir`` is set; the
     single-entity lookups still hit the API for now (same TODO as the
     GitLab client -- wiring is straightforward for Zenodo because the API
     returns plain JSON).
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import httpx

logger = logging.getLogger(__name__)


class ZenodoClient:
    """HTTP client for Zenodo's records / communities / users endpoints.

    Authentication: ``Authorization: Bearer <token>`` when ``tokens`` is
    non-empty. Anonymous (no header) when ``tokens=[]`` -- Zenodo's public
    records and communities are readable without auth.
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
                "ZenodoClient(%s) constructed with no tokens -- "
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
